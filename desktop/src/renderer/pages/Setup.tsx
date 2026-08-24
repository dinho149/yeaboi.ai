// Setup — the provider wizard, four steps like the TUI's: pick a provider,
// enter (and live-verify) its credential, pick a model (live-discovered,
// merged with the curated presets), save. Anthropic offers the subscription
// sign-in as an alternative to pasting a key; the token then lives on the
// backend and never passes through this renderer.

import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { SignInPanel } from '../components/SignInPanel';
import {
  type ProviderCard,
  type ProviderCatalog,
  discoverModels,
  loadProviders,
  saveSetting,
  verifyProvider,
} from '../settings';

const STEPS = ['Provider', 'Credential', 'Model', 'Done'] as const;

export function Setup() {
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [error, setError] = useState('');
  const [step, setStep] = useState(0);
  const [provider, setProvider] = useState<ProviderCard | null>(null);

  // Credential step state. For the subscription path the credential stays '',
  // the backend already holds the token.
  const [authMode, setAuthMode] = useState<'api_key' | 'subscription'>('api_key');
  const [credential, setCredential] = useState('');
  const [signedIn, setSignedIn] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verdict, setVerdict] = useState<{ ok: boolean; message: string } | null>(null);

  // Model step state.
  const [models, setModels] = useState<string[]>([]);
  const [hints, setHints] = useState<Record<string, string>>({});
  const [discovering, setDiscovering] = useState(false);
  const [model, setModel] = useState('');
  const [custom, setCustom] = useState('');
  const [saveError, setSaveError] = useState('');

  useEffect(() => {
    loadProviders().then(setCatalog, (e: Error) => setError(e.message));
  }, []);

  if (error) return <p>Could not load the provider catalog: {error}</p>;
  if (!catalog) return <p>Loading…</p>;

  const pickProvider = (card: ProviderCard) => {
    setProvider(card);
    setCredential(card.is_base_url_input ? (card.default_input ?? '') : '');
    setAuthMode('api_key');
    setSignedIn(false);
    setVerdict(null);
    setStep(1);
  };

  const enterModelStep = () => {
    if (!provider) return;
    setStep(2);
    setModel(provider.models.default);
    setModels(provider.models.presets);
    setHints(provider.model_hints ?? {});
    setDiscovering(true);
    discoverModels(provider.provider_val, credential).then(
      (result) => {
        setModels(result.models.length ? result.models : provider.models.presets);
        setHints(result.hints);
        setDiscovering(false);
      },
      () => setDiscovering(false),
    );
  };

  const verify = () => {
    if (!provider) return;
    setVerifying(true);
    setVerdict(null);
    verifyProvider(provider.provider_val, credential).then(
      (result) => {
        setVerdict(result);
        setVerifying(false);
        if (result.ok) enterModelStep();
      },
      (e: Error) => {
        setVerdict({ ok: false, message: e.message });
        setVerifying(false);
      },
    );
  };

  const finish = async () => {
    if (!provider) return;
    const chosen = model === '__custom__' ? custom.trim() : model;
    if (!chosen) return;
    try {
      await saveSetting('LLM_PROVIDER', provider.provider_val);
      if (provider.provider_val === 'anthropic') {
        // The subscription sign-in already persisted its token + auth mode.
        if (authMode === 'api_key') {
          await saveSetting('ANTHROPIC_AUTH_MODE', 'api_key');
          if (credential) await saveSetting('ANTHROPIC_API_KEY', credential);
        }
      } else if (credential) {
        await saveSetting(provider.env_var, credential);
      }
      await saveSetting('LLM_MODEL', chosen);
      setStep(3);
    } catch (e) {
      setSaveError((e as Error).message);
    }
  };

  const subscription = provider?.provider_val === 'anthropic' && authMode === 'subscription';
  const credentialReady = subscription ? signedIn : credential.trim().length > 0;

  return (
    <div>
      <h1 class="page-title">Setup</h1>
      <nav class="wizard-steps">
        {STEPS.map((name, i) => (
          <span key={name} class={i === step ? 'step active' : i < step ? 'step complete' : 'step'}>
            {i + 1}. {name}
          </span>
        ))}
      </nav>

      {step === 0 && (
        <div class="card-grid">
          {catalog.providers.map((card) => (
            <button key={card.provider_val} class="mode-card provider-card" onClick={() => pickProvider(card)}>
              <h3>{card.full_name}</h3>
              <p>{card.tagline}</p>
              <p class="provider-instructions">{card.instructions}</p>
            </button>
          ))}
        </div>
      )}

      {step === 1 && provider && (
        <section class="settings-box wizard-pane">
          <h2>{provider.full_name}</h2>
          <p class="provider-instructions">{provider.instructions}</p>

          {provider.provider_val === 'anthropic' && (
            <div class="settings-row">
              <span class="settings-label">Auth</span>
              <span class="settings-choices">
                <button class={authMode === 'api_key' ? 'choice active' : 'choice'} onClick={() => setAuthMode('api_key')}>
                  api key
                </button>
                <button
                  class={authMode === 'subscription' ? 'choice active' : 'choice'}
                  onClick={() => setAuthMode('subscription')}
                >
                  subscription
                </button>
              </span>
            </div>
          )}

          {subscription ? (
            <div>
              {signingIn ? (
                <SignInPanel
                  onClose={(saved, message) => {
                    setSigningIn(false);
                    setSignedIn(saved);
                    setVerdict({ ok: saved, message });
                  }}
                />
              ) : (
                <button onClick={() => setSigningIn(true)}>{signedIn ? 'Sign in again' : 'Sign in with Claude…'}</button>
              )}
            </div>
          ) : (
            <div class="settings-row">
              <span class="settings-label">
                {provider.is_region_input ? 'AWS Region' : provider.is_base_url_input ? 'Server URL' : 'API Key'}
              </span>
              <input
                class="wizard-credential"
                type={provider.is_region_input || provider.is_base_url_input ? 'text' : 'password'}
                value={credential}
                placeholder={provider.prefix ? `${provider.prefix}…` : ''}
                onInput={(event) => setCredential((event.target as HTMLInputElement).value)}
              />
            </div>
          )}

          {verdict && <p class={verdict.ok ? 'signin-ok' : 'signin-error'}>{verdict.message}</p>}

          <div class="settings-dialog-actions">
            <button onClick={() => setStep(0)}>Back</button>
            {subscription ? (
              <button disabled={!credentialReady} onClick={enterModelStep}>
                Continue
              </button>
            ) : (
              <>
                <button disabled={!credentialReady || verifying} onClick={verify}>
                  {verifying ? 'Verifying…' : 'Verify & continue'}
                </button>
                {verdict && !verdict.ok && (
                  <button disabled={!credentialReady} onClick={enterModelStep}>
                    Use anyway
                  </button>
                )}
              </>
            )}
          </div>
        </section>
      )}

      {step === 2 && provider && (
        <section class="settings-box wizard-pane">
          <h2>Model</h2>
          {discovering && <p class="signin-waiting">Discovering what this credential can run…</p>}
          <div class="model-list">
            {models.map((id) => (
              <label key={id} class={model === id ? 'model-option active' : 'model-option'}>
                <input type="radio" name="model" checked={model === id} onChange={() => setModel(id)} />
                <code>{id}</code>
                {id === provider.models.default && <span class="model-tag">recommended</span>}
                {hints[id] && <span class="model-hint">{hints[id]}</span>}
              </label>
            ))}
            <label class={model === '__custom__' ? 'model-option active' : 'model-option'}>
              <input type="radio" name="model" checked={model === '__custom__'} onChange={() => setModel('__custom__')} />
              <span>Custom…</span>
              {model === '__custom__' && (
                <input
                  class="wizard-credential"
                  value={custom}
                  placeholder="model id"
                  onInput={(event) => setCustom((event.target as HTMLInputElement).value)}
                />
              )}
            </label>
          </div>
          {saveError && <p class="signin-error">{saveError}</p>}
          <div class="settings-dialog-actions">
            <button onClick={() => setStep(1)}>Back</button>
            <button disabled={model === '__custom__' && !custom.trim()} onClick={() => void finish()}>
              Save
            </button>
          </div>
        </section>
      )}

      {step === 3 && provider && (
        <section class="settings-box wizard-pane wizard-done">
          <Duck state="joined" size={56} />
          <h2>You&apos;re set</h2>
          <p>
            {provider.full_name} · <code>{model === '__custom__' ? custom.trim() : model}</code>
          </p>
          <div class="settings-dialog-actions">
            <a href="#/home">
              <button>Go to Home</button>
            </a>
            <a href="#/settings/credentials">
              <button>Open Settings</button>
            </a>
          </div>
        </section>
      )}
    </div>
  );
}
