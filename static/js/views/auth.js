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

function layout(root, card, pitch = ['Ce soir, c’est séance à la maison.',
  'Films, séries et animes de ton serveur, sur le navigateur, la TV ou le téléphone.']) {
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
  const btn = h('button.btn.primary.block', { type: 'submit' }, 'Entrer dans la salle');
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
  }, brand('Bon retour', 'Connecte-toi avec ton compte Nexis.'),
  field('Identifiant', user), field('Mot de passe', pass), err, btn,
  h('p.auth-foot', 'Mot de passe oublié ? Demande à l’administrateur du serveur.'));
  layout(root, form);
}

export function renderSetup(root, onDone) {
  const code = h('input', { autocomplete: 'off', placeholder: 'Ex. 4F2A9C1B', style: { textTransform: 'uppercase' } });
  const username = h('input', { autocomplete: 'username', value: 'admin' });
  const display = h('input', { placeholder: 'Ton prénom' });
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
        toast('Bienvenue ! Ajoute maintenant tes bibliothèques.', 'ok');
        onDone();
      } catch (ex) { err.textContent = ex.message; } finally { btn.disabled = false; }
    },
  }, brand('Bienvenue', 'Première installation : crée le compte administrateur. Tu ajouteras ensuite tes dossiers de films et de séries.'),
  h('h3.step', h('span', '1'), 'Code d’installation'),
  field('Code affiché au démarrage', code, 'Visible avec : journalctl -u seance | grep code — ou dans /var/lib/seance/setup-code.txt'),
  h('h3.step', h('span', '2'), 'Compte administrateur'),
  h('div.grid2', field('Identifiant', username), field('Nom affiché', display)),
  h('div.grid2', field('Mot de passe (8 caractères min.)', pass), field('Confirmation', pass2)),
  err, btn);
  layout(root, form, ['Installons ta salle de cinéma.', 'Quelques secondes pour créer le compte administrateur, puis Nexis range tes films et tes séries.']);
}
