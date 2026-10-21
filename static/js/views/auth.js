import { get, post } from '../api.js';
import { clear, field, h, toast, toggle } from '../ui.js';

function brand() {
  return h('div.auth-brand', h('span.logo-mark.big'), h('h1', 'Séance'));
}

export function renderLogin(root, onDone) {
  const user = h('input', { name: 'username', autocomplete: 'username', required: true, autofocus: true });
  const pass = h('input', { name: 'password', type: 'password', autocomplete: 'current-password', required: true });
  const err = h('p.form-error', { role: 'alert' });
  const btn = h('button.btn.primary.block', { type: 'submit' }, 'Se connecter');
  const form = h('form.auth-card', {
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = '';
      btn.disabled = true;
      try {
        await post('/api/login', { username: user.value.trim(), password: pass.value }, { quiet401: true });
        await onDone();
      } catch (ex) {
        err.textContent = ex.message;
        pass.select();
      } finally { btn.disabled = false; }
    },
  }, brand(), h('p.muted.center', 'Connecte-toi pour retrouver ton espace.'),
  field('Identifiant', user), field('Mot de passe', pass), err, btn);
  clear(root, h('div.auth-page', form));
}

// Shared Plex connection block (setup wizard + admin page).
export function plexConnector({ setupCode, initialUrl = '', tokenSet = false } = {}) {
  const headers = setupCode ? () => ({ 'X-Setup-Code': setupCode() }) : () => ({});
  const url = h('input', { value: initialUrl || `http://${location.hostname}:32400`, placeholder: 'http://192.168.1.10:32400' });
  const token = h('input', { type: 'password', autocomplete: 'off',
    placeholder: tokenSet ? 'Jeton enregistré (laisser vide pour le garder)' : 'Colle ton X-Plex-Token' });
  const verify = toggle('Vérifier le certificat HTTPS', true);
  const status = h('div.plex-status');
  let pinId = null;
  let polling = null;

  const pinBtn = h('button.btn.plex', { type: 'button', onclick: async () => {
    try {
      const pin = await post('/api/plex/pin', {}, { headers: headers() });
      pinId = String(pin.id);
      window.open(pin.auth_url, '_blank', 'noopener');
      status.className = 'plex-status wait';
      status.textContent = 'Autorise Séance dans l’onglet plex.tv qui vient de s’ouvrir…';
      clearInterval(polling);
      let tries = 0;
      polling = setInterval(async () => {
        if (++tries > 150) { clearInterval(polling); status.textContent = 'Délai dépassé, recommence.'; return; }
        try {
          const r = await get(`/api/plex/pin/${pinId}`, { headers: headers() });
          if (r.authorized) {
            clearInterval(polling);
            token.value = '';
            token.placeholder = 'Compte Plex lié ✓';
            status.className = 'plex-status ok';
            status.textContent = 'Compte Plex autorisé. Teste maintenant la connexion.';
          }
        } catch (e) { clearInterval(polling); status.className = 'plex-status bad'; status.textContent = e.message; }
      }, 2000);
    } catch (e) { status.className = 'plex-status bad'; status.textContent = e.message; }
  } }, 'Se connecter avec Plex');

  const values = () => ({ plex_url: url.value.trim(), plex_token: token.value.trim() || undefined,
    pin_id: pinId || undefined, verify_tls: verify.querySelector('input').checked });

  const testBtn = h('button.btn', { type: 'button', onclick: async () => {
    status.className = 'plex-status wait';
    status.textContent = 'Test en cours…';
    try {
      const r = await post('/api/plex/test', values(), { headers: headers() });
      const libs = r.libraries.filter((l) => ['movie', 'show'].includes(l.type)).map((l) => l.title).join(', ');
      status.className = 'plex-status ok';
      status.textContent = `Connecté à « ${r.name} » (Plex ${r.version}). Bibliothèques : ${libs || 'aucune'}.`;
    } catch (e) { status.className = 'plex-status bad'; status.textContent = e.message; }
  } }, 'Tester la connexion');

  const el = h('div.plex-connector',
    field('Adresse du serveur Plex', url, 'Depuis la machine où tourne Séance. Exemple : http://192.168.1.20:32400'),
    h('div.or-split', h('div', pinBtn), h('span.muted', 'ou'),
      h('div.grow', field('Jeton Plex (X-Plex-Token)', token,
        'Dans Plex : ouvre un média → ⋯ → Infos → Afficher le XML : le jeton est à la fin de l’URL.'))),
    verify, h('div.actions-row', testBtn), status);
  return { el, values, stop: () => clearInterval(polling) };
}

export function renderSetup(root, onDone) {
  const code = h('input', { autocomplete: 'off', placeholder: 'Ex. 4F2A9C1B', style: { textTransform: 'uppercase' } });
  const username = h('input', { autocomplete: 'username', value: 'admin' });
  const display = h('input', { placeholder: 'Ton prénom' });
  const pass = h('input', { type: 'password', autocomplete: 'new-password' });
  const pass2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const err = h('p.form-error', { role: 'alert' });
  const plex = plexConnector({ setupCode: () => code.value.trim() });
  const btn = h('button.btn.primary.block', { type: 'submit' }, 'Terminer l’installation');

  const form = h('form.auth-card.wide', {
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = '';
      if (pass.value !== pass2.value) { err.textContent = 'Les mots de passe ne correspondent pas.'; return; }
      btn.disabled = true;
      try {
        const r = await post('/api/setup', { username: username.value.trim(), display_name: display.value.trim() || undefined,
          password: pass.value, ...plex.values() }, { headers: { 'X-Setup-Code': code.value.trim() } });
        plex.stop();
        toast(`Bienvenue ! Connecté à ${r.server}.`, 'ok');
        onDone();
      } catch (ex) { err.textContent = ex.message; } finally { btn.disabled = false; }
    },
  }, brand(),
  h('p.muted.center', 'Première installation : crée le compte administrateur et relie ton serveur Plex.'),
  h('h3.step', h('span', '1'), 'Code d’installation'),
  field('Code affiché au démarrage', code, 'Visible avec : journalctl -u seance | grep code — ou dans /var/lib/seance/setup-code.txt'),
  h('h3.step', h('span', '2'), 'Compte administrateur'),
  h('div.grid2', field('Identifiant', username), field('Nom affiché', display)),
  h('div.grid2', field('Mot de passe (8 caractères min.)', pass), field('Confirmation', pass2)),
  h('h3.step', h('span', '3'), 'Serveur Plex'),
  plex.el, err, btn);
  clear(root, h('div.auth-page', form));
}
