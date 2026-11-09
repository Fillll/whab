"""Host-owned bank generations; candidate engines receive data-only readiness."""

import asyncio
import inspect
from collections.abc import Mapping
from importlib import metadata

from starlette.concurrency import run_in_threadpool

from whab.budgets.runtime import RuntimeCandidate, RuntimeResource, RuntimeTransitionError
from whab.plugins import BankContext
from whab.plugins.bank_adapter import BankAdapter, BankProviderContext, ProviderFailure
from whab.plugins.bank_continuations import BankContinuations
from whab.plugins.bank_crypto import BankCipher
from whab.plugins.effect_store import EffectError
from whab.plugins.provider_worker import WorkerBankFactory
from whab.plugins.report_worker import ReportWorker
from whab.plugins.runtime import PluginError
from whab.plugins.sdk import valid_plugin_name
from whab.storage.bank import BankStore

from .bank_connections import REFERENCE, AttachedBankStore, BankConnections
from .bank_profiles import BankProfiles
from .bank_runtime import BankCoordinator, OnDemandBankProvider


def live_bank(plugin):
    from whab.plugins.worker_adapters import supports_live_plugin
    return type(plugin.bank) is BankAdapter and supports_live_plugin(plugin)


class UnconfiguredBank:
    """A missing host key cannot create a plaintext or throwaway private journal."""

    def __init__(self, provider):
        self.provider = provider
        self.config = {**provider.config, 'configured': False,
                       'missingConfig': list(dict.fromkeys((*provider.config['missingConfig'], 'TOKEN_ENCRYPTION_KEY')))}
        self._closed = False

    async def begin_link(self, item_id=None):
        raise ProviderFailure('not_configured')

    async def complete_link(self, flow_id, public_token=None, institution_name=None):
        raise ProviderFailure('not_configured')

    async def continuation_status(self, flow_id):
        raise EffectError('continuation_missing')

    async def cancel_link(self, flow_id):
        raise EffectError('continuation_missing')

    async def sync(self, item_id=None):
        raise ProviderFailure('not_configured')

    async def disconnect(self, item_id):
        raise ProviderFailure('not_configured')

    async def webhook(self, raw, headers):
        from whab.plugins import WebhookResult
        return WebhookResult(False, 401)

    def prepare_close(self):
        return None

    async def close(self):
        if not self._closed:
            self._closed = True
            await run_in_threadpool(self.provider.close)


class BankLifecycle:
    """Retain journal owners across enable/disable and immutable worker upgrades.

    The application owns this lifecycle before returning, including an unstarted
    CLI-created app. Candidate factories open clients but perform no provider HTTP.
    A changed bank must pass drained continuation/background guards first.
    """

    def __init__(self, settings, tracked, budget_id, *, home, context_factory=None, suggestions_only=False, allow_banks=True):
        self.settings, self.tracked, self.budget_id, self.home = settings, tracked, budget_id, home
        self.context_factory, self.suggestions_only = context_factory, suggestions_only
        self.journals, self.services = {}, []
        self.descriptors, self.resources = {}, {}
        self._workers = set()
        self.background_scope = None
        self.allow_banks = allow_banks
        self.automatic_sync = lambda _owner: True
        self.shared_event = None
        self._closed = False
        try:
            self.cipher = BankCipher(settings.encryption_key)
        except ValueError:
            self.cipher = None
        self.connections = BankConnections(settings.home, settings.plaid_env, self.cipher) if self.cipher and allow_banks else None
        self.shared_providers = set()
        self._connection_scopes = {}
        self.profile_keys = {}
        directory = home / 'plugin-workers' / 'bank-continuations' / settings.plaid_env
        try:
            if directory.exists():
                for child in directory.iterdir():
                    if len(self.journals) >= 100 or not valid_plugin_name(child.name) or child.is_symlink() or not child.is_dir():
                        raise EffectError('invalid_effect')
                    self._journal(child.name)
        except BaseException:
            for journal in self.journals.values():
                journal.close()
            raise

    def _journal(self, owner):
        if self._closed or self.cipher is None:
            raise EffectError('effect_unavailable')
        if owner not in self.journals:
            if len(self.journals) >= 100:
                raise EffectError('effect_unavailable')
            root = self.home / 'plugin-workers' / 'bank-continuations' / self.settings.plaid_env / owner
            self.journals[owner] = BankContinuations(root, self.budget_id, self.settings.plaid_env, owner,
                seal=self.cipher.seal, open=self.cipher.open)
        return self.journals[owner]

    def bank_store(self, owner):
        store = BankStore(self.tracked, owner, suggestions_only=self.suggestions_only,
            scoped_items=lambda item: self.scoped_connection(owner, item))
        return AttachedBankStore(store, self.connections, self.budget_id) if owner in self.shared_providers else store

    def scoped_connection(self, owner, item):
        if owner not in self.shared_providers:
            return False
        key = owner, item
        if key not in self._connection_scopes:
            # Profile identity is immutable. Read it once per connection rather
            # than rereading/decrypting the registry for every transaction ID.
            self._connection_scopes[key] = self.connections.get(owner, item)['profileId'] != 'default'
        return self._connection_scopes[key]

    def migrate_connections(self, owner, identity_keys=()):
        """No provider calls; retain native cursor/settings and recovery evidence."""
        from whab.hosting import process_lock
        if self.connections is None:
            return
        store = BankStore(self.tracked, owner)
        profiles = self.account_profiles(owner, identity_keys) if identity_keys else None
        principal = (profiles.principal(profiles.config('default'))
            if profiles and any(row['profileId'] == 'default' for row in profiles.public()) else None)
        with process_lock(self.connections.directory / (owner + '-operations.lock')), self.tracked.lock:
            for summary in store.items():
                item = store.get_item(summary['id'])
                self.connections.register(owner, {**item, 'storedIdentity': store.item_identity(item['id'])},
                    self.budget_id, [row for row in self.tracked.accounts('personal')
                                     if row.get('itemId') == store.item_identity(item['id'])],
                    profile_principal=principal if not item['id'].startswith('shared:') else None)
                if not item['token'].startswith(REFERENCE):
                    store.save_item(item['id'], self.connections.reference(owner, item['id']), item['institutionName'])
                    store.set_item_status(item['id'], item['status'], item.get('error'))
        self.shared_providers.add(owner)

    def account_profiles(self, owner, identity_keys):
        config = {key: value for key, value in self.settings.bank_configuration(owner).items()
            if key.casefold() not in {'token_encryption_key', 'whab_encryption_key', 'encryption_key'}}
        return BankProfiles(self.connections, owner, self.budget_id, identity_keys, config)

    def _provider(self, plugin):
        # Validate the bounded config before acquiring an installed child.
        config = {key: value for key, value in self.settings.bank_configuration(plugin.id).items()
                  if key.casefold() not in {'token_encryption_key', 'whab_encryption_key', 'encryption_key'}}
        context = BankProviderContext(self.budget_id, self.settings.plaid_env, config)
        factory = plugin.bank.factory
        if not isinstance(factory, WorkerBankFactory):
            entries = tuple(entry for entry in metadata.entry_points(group='whab.plugins')
                            if entry.name == plugin.id and getattr(entry, 'dist', None) is not None)
            if len(entries) != 1:
                raise EffectError('effect_unavailable')
            entry, = entries
            if len(self._workers) >= 100:
                raise EffectError('effect_unavailable')
            distribution, version = entry.dist.metadata['Name'], entry.dist.version

            def acquire(config_override=None):
                worker = ReportWorker(plugin.id, expected_distribution=distribution, expected_version=version,
                                      on_create=self._workers.add)
                self._workers.add(worker)
                scoped = BankProviderContext(self.budget_id, self.settings.plaid_env, config_override) if config_override is not None else context
                return WorkerBankFactory(worker, owns_worker=True)(scoped)

            def release(provider):
                try:
                    provider.close()
                finally:
                    if not provider.worker.cleanup_pending:
                        self._workers.discard(provider.worker)

            return OnDemandBankProvider(acquire, release), f'{distribution}/{version}'
        return factory(context), factory.code_identity

    def create(self, plugin, generation, injected=None):
        provider = None
        try:
            if injected is not None:
                service = injected
            elif type(plugin.bank) is BankAdapter:
                self.profile_keys[plugin.id] = plugin.bank.credential_identity_keys
                if not live_bank(plugin):
                    raise EffectError('effect_unavailable')
                provider, identity = self._provider(plugin)
                if self.cipher is None:
                    service = UnconfiguredBank(provider)
                else:
                    profiles = self.account_profiles(plugin.id, plugin.bank.credential_identity_keys) if plugin.bank.credential_identity_keys else None
                    self.migrate_connections(plugin.id, plugin.bank.credential_identity_keys)
                    service = BankCoordinator(provider, self.tracked, self.bank_store(plugin.id),
                        budget_id=self.budget_id, environment=self.settings.plaid_env, provider_id=plugin.id,
                        generation=generation, journal=self._journal(plugin.id), cipher=self.cipher,
                        code_identity=identity, background_scope=self.background_scope,
                        requires_account_setup=plugin.bank.requires_account_setup, supports_investments=plugin.bank.supports_investments,
                        automatic_sync=lambda: self.automatic_sync(plugin.id), connections=self.connections,
                        shared_event=self.shared_event, profiles=profiles)
            else:
                service = plugin.bank(BankContext(self.bank_store(plugin.id), self.settings.bank_configuration(plugin.id),
                    self.settings.plaid_env, self.settings.encryption_key,
                    budget=self.context_factory(plugin.id) if self.context_factory else None,
                    background_scope=self.background_scope))
            if inspect.iscoroutine(service):
                service.close()
            continued = type(plugin.bank) is BankAdapter if injected is None else any(
                callable(getattr(service, name, None)) for name in ('begin_link', 'complete_link', 'continuation_status', 'cancel_link'))
            required = ('sync', 'disconnect', 'webhook', 'close') + (
                ('begin_link', 'complete_link', 'continuation_status', 'cancel_link') if continued else ('link_token', 'exchange'))
            if injected is None and (inspect.isawaitable(service) or not isinstance(getattr(service, 'config', None), Mapping)
                    or any(not callable(getattr(service, name, None)) for name in required)):
                raise ValueError()
            self.descriptors[id(service)] = plugin
            self.services.append(service)
            return service
        except BaseException as error:
            if provider is not None:
                try:
                    provider.close()
                except Exception:
                    pass
            if not isinstance(error, Exception):
                raise
            raise PluginError('Bank plugin could not start. Check its configuration or disable it.',
                              code='bank_startup_failed', plugin_id=plugin.id) from None

    def preflight(self, active, plugins, *, changed=None):
        desired = {plugin.id: plugin for plugin in plugins if plugin.bank is not None}
        if desired and not self.allow_banks:
            raise PluginError('Bank ingestion belongs on the authoritative server.', code='invalid_configuration',
                              plugin_id=next(iter(desired)))
        for owner, service in active.items():
            if (changed is not None and owner in changed) or (changed is None and self.descriptors.get(id(service)) is not desired.get(owner)):
                guard = getattr(service, 'prepare_close', None)
                if not callable(guard):
                    raise PluginError('This provider needs a restart to change its selection.', code='restart_required', plugin_id=owner)
                try:
                    guard()
                except EffectError:
                    raise RuntimeTransitionError('continuation_busy', owner) from None

    async def close_service(self, service):
        provider = getattr(service, 'provider', None)
        worker = getattr(provider, 'worker', None)
        closing = getattr(service, '_closing', None)
        cached_failure = isinstance(closing, asyncio.Future) and closing.done() and (closing.cancelled() or closing.exception() is not None)
        if worker is not None and (worker.cleanup_pending or cached_failure):
            await run_in_threadpool(worker.close)
            if worker.cleanup_pending:
                raise PluginError('The bank worker still has pending cleanup.', code='worker_cleanup_pending',
                                  plugin_id=worker.plugin_id)
        if cached_failure and worker is not None:
            if getattr(service, 'owns_journal', False):
                await run_in_threadpool(service.journal.close)
        else:
            await service.close()
        if worker is not None and worker.cleanup_pending:
            raise PluginError('The bank worker still has pending cleanup.', code='worker_cleanup_pending',
                              plugin_id=worker.plugin_id)
        if service in self.services:
            self.services.remove(service)
        self.descriptors.pop(id(service), None)
        self.resources.pop(id(service), None)
        if worker is not None and getattr(provider, 'owns_worker', False) and not worker.healthy:
            self._workers.discard(worker)

    def finalize(self, preparation, candidate):
        active = preparation.active.services['banks']
        plugins = candidate.services['plugins'].bank_plugins()
        self.preflight(active, plugins)
        banks, removed, resources = {}, set(), []
        desired = {plugin.id: plugin for plugin in plugins}
        for owner, service in active.items():
            if self.descriptors.get(id(service)) is desired.get(owner):
                banks[owner] = service
            else:
                removed.add(id(self.resources.get(id(service))))
        for plugin in plugins:
            if plugin.id in banks:
                continue
            holder = []
            async def close(holder=holder):
                if holder:
                    await self.close_service(holder[0])
            resource = preparation.own(RuntimeResource(plugin.id, close))
            service = self.create(plugin, preparation.active.number + 1)
            holder.append(service)
            self.resources[id(service)] = resource
            banks[plugin.id] = service
            resources.append(resource)
        return RuntimeCandidate({**candidate.services, 'banks': banks},
            tuple(resource for resource in candidate.resources if id(resource) not in removed) + tuple(resources))

    def release_journals(self):
        """Composition failure before lifespan: no provider engine has started."""
        for journal in self.journals.values():
            journal.close()
        self.journals.clear()

    async def close(self):
        if self._closed and not (self.services or self._workers or self.journals):
            return
        self._closed = True
        try:
            for service in reversed(tuple(self.services)):
                try:
                    await self.close_service(service)
                except Exception:
                    pass
        finally:
            try:
                pending = []
                for worker in tuple(self._workers):
                    try:
                        await run_in_threadpool(worker.close)
                    except Exception:
                        pending.append(worker)
                    else:
                        if worker.cleanup_pending:
                            pending.append(worker)
                        else:
                            self._workers.discard(worker)
                if pending:
                    raise PluginError('The bank worker still has pending cleanup.', code='worker_cleanup_pending',
                                      plugin_id=pending[0].plugin_id)
            finally:
                self.release_journals()
