import { post } from '../api.js';
import { clear, field, h, toast } from '../ui.js';

// Décor de la page de connexion : un mur d'affiches stylisées (sans contenu de la médiathèque, la page est publique)
const WALL = ['#3a2618', '#18293a', '#2b2b36', '#3a1e1e', '#1e3328', '#36301c', '#241c36', '#14303a', '#3a2a12', '#202a3a'];

function stage(title, text) {
  const tiles = [];
  for (let i = 0; i < 36; i++) tiles.push(h('span', { style: { '--c': WALL[(i * 7) % WALL.length] } }));
  return h('section.auth-stage', { 'aria-hidden': 'false' },
    h('div.auth-wall', { 'aria-hidden': 'true' }, tiles),
    h('div.auth-stage-body',
      h('div.logo', h('span.logo-mark', { role: 'img', 'aria-label': 'Logo Nexis' }), 'Nexis'),
      h('div.auth-pitch', h('h2', title), h('p', text))));
}

function layout(root, card, pitch = ['Votre médiathèque, partout.',
  'Films, séries et animes, dans le navigateur, sur la TV ou sur le téléphone.']) {
  clear(root, h('div.auth-page', stage(...pitch), h('section.auth-side', card)));
}

function brand(title, lead) {
  return [h('div.auth-brand', h('span.logo-mark.big', { role: 'img', 'aria-label': 'Logo Nexis' }), h('h1', title)),
    h('p.auth-lead', lead)];
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
        const r = await post('/api/login', { username: user.value.trim(), password: pass.value }, { quiet401: true });
        if (r && r.two_factor) { secondStep(r.ticket); return; }
        await onDone();
      } catch (ex) {
        err.textContent = ex.message;
        pass.select();
      } finally { btn.disabled = false; }
    },
  }, brand('Connexion', 'Identifiez-vous avec votre compte Nexis.'),
  field('Identifiant', user), field('Mot de passe', pass), err, btn,
  h('p.auth-foot', 'Mot de passe oublié ? Contactez l’administrateur du serveur.'));
  layout(root, form);

  // double authentification : le mot de passe est bon, le code de l'application est demandé
  function secondStep(ticket) {
    const code = h('input', { name: 'code', autocomplete: 'one-time-code', autocapitalize: 'none', maxlength: 9,
      placeholder: '123456', required: true, autofocus: true, style: { letterSpacing: '0.2em', fontSize: '20px' } });
    const err2 = h('p.form-error', { role: 'alert' });
    const ok = h('button.btn.primary.block', { type: 'submit' }, 'Valider');
    const card = h('form.auth-card', {
      onsubmit: async (e) => {
        e.preventDefault();
        err2.textContent = '';
        ok.disabled = true;
        try {
          const r = await post('/api/login/2fa', { ticket, code: code.value }, { quiet401: true });
          if (r.recovery_used) {
            toast(r.recovery_left ? `Code de secours utilisé. Il en reste ${r.recovery_left}.`
              : 'Dernier code de secours utilisé : générez-en de nouveaux dans Paramètres → Profil.', 'ok');
          }
          await onDone();
        } catch (ex) {
          err2.textContent = ex.message;
          if (ex.status === 401 && /Délai/.test(ex.message)) setTimeout(() => renderLogin(root, onDone), 1500);
          code.select();
        } finally { ok.disabled = false; }
      },
    }, brand('Double authentification', 'Saisissez le code à 6 chiffres affiché par votre application d’authentification.'),
    field('Code', code, 'Sans le téléphone : un code de secours (xxxx-xxxx) fonctionne aussi, une seule fois chacun.'), err2, ok,
    h('p.auth-foot', h('a', { href: '#/login', onclick: (e) => { e.preventDefault(); renderLogin(root, onDone); } }, 'Revenir à la connexion')));
    layout(root, card);
    setTimeout(() => code.focus(), 30);
  }
}

export function renderSetup(root, onDone) {
  const code = h('input', { autocomplete: 'off', placeholder: 'Ex. 4F2A9C1B', style: { textTransform: 'uppercase' } });
  const username = h('input', { autocomplete: 'username', value: 'admin' });
  const display = h('input', { placeholder: 'Prénom' });
  const pass = h('input', { type: 'password', autocomplete: 'new-password' });
  const pass2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const err = h('p.form-error', { role: 'alert' });
  const btn = h('button.btn.primary.block', { type: 'submit' }, 'Terminer l’installation');

  const form = h('form.auth-card.wide', {
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = '';
      if (pass.value !== pass2.value) { err.textContent = 'Les mots de passe ne correspondent pas.'; return; }
      btn.disabled = true;
      try {
        await post('/api/setup', { username: username.value.trim(), display_name: display.value.trim() || undefined,
          password: pass.value }, { headers: { 'X-Setup-Code': code.value.trim() } });
        toast('Compte administrateur créé. Ajoutez maintenant vos bibliothèques.', 'ok');
        onDone();
      } catch (ex) { err.textContent = ex.message; } finally { btn.disabled = false; }
    },
  }, brand('Installation', 'Création du compte administrateur. Les dossiers de films et de séries s’ajoutent à l’étape suivante.'),
  h('h3.step', h('span', '1'), 'Code d’installation'),
  field('Code affiché au démarrage', code, 'Affiché dans le journal du service (journalctl -u seance) et enregistré dans /var/lib/seance/setup-code.txt.'),
  h('h3.step', h('span', '2'), 'Compte administrateur'),
  h('div.grid2', field('Identifiant', username), field('Nom affiché', display)),
  h('div.grid2', field('Mot de passe (8 caractères min.)', pass), field('Confirmation', pass2)),
  err, btn);
  layout(root, form, ['Installation de Nexis', 'Création du compte administrateur, puis ajout des bibliothèques.']);
}
