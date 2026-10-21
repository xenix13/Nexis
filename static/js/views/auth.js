import { post } from '../api.js';
import { clear, field, h, toast } from '../ui.js';

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

export function renderSetup(root, onDone) {
  const code = h('input', { autocomplete: 'off', placeholder: 'Ex. 4F2A9C1B', style: { textTransform: 'uppercase' } });
  const username = h('input', { autocomplete: 'username', value: 'admin' });
  const display = h('input', { placeholder: 'Ton prénom' });
  const pass = h('input', { type: 'password', autocomplete: 'new-password' });
  const pass2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const err = h('p.form-error', { role: 'alert' });
  const btn = h('button.btn.primary.block', { type: 'submit' }, 'Terminer l’installation');

  const form = h('form.auth-card', {
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
  }, brand(),
  h('p.muted.center', 'Première installation : crée le compte administrateur. Tu ajouteras ensuite tes dossiers de films et de séries.'),
  h('h3.step', h('span', '1'), 'Code d’installation'),
  field('Code affiché au démarrage', code, 'Visible avec : journalctl -u seance | grep code — ou dans /var/lib/seance/setup-code.txt'),
  h('h3.step', h('span', '2'), 'Compte administrateur'),
  h('div.grid2', field('Identifiant', username), field('Nom affiché', display)),
  h('div.grid2', field('Mot de passe (8 caractères min.)', pass), field('Confirmation', pass2)),
  err, btn);
  clear(root, h('div.auth-page', form));
}
