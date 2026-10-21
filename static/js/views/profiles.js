// « Qui regarde ? » : changement de profil du foyer, avec code PIN.
import { get, post } from '../api.js';
import { avatar, clear, field, h, modal, toast } from '../ui.js';

function pinPad(onSubmit, length = 4) {
  const input = h('input.pin-input', { type: 'password', inputmode: 'numeric', autocomplete: 'off', maxlength: 8,
    pattern: '[0-9]*', 'aria-label': 'Code PIN' });
  const dots = h('div.pin-dots');
  const paint = () => {
    dots.replaceChildren(...Array.from({ length: Math.max(length, input.value.length) }, (_, i) =>
      h('span.pin-dot', { class: i < input.value.length ? 'pin-dot on' : 'pin-dot' })));
  };
  const press = (d) => {
    if (d === '⌫') input.value = input.value.slice(0, -1);
    else if (input.value.length < 8) input.value += d;
    paint();
    if (input.value.length >= length && d !== '⌫') onSubmit(input.value, reset);
  };
  const reset = () => { input.value = ''; paint(); input.focus(); };
  input.addEventListener('input', () => {
    input.value = input.value.replace(/\D/g, '');
    paint();
    if (input.value.length >= length) onSubmit(input.value, reset);
  });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && input.value.length >= 4) onSubmit(input.value, reset); });
  paint();
  setTimeout(() => input.focus(), 50);
  return h('div.pin-pad', input, dots,
    h('div.pin-keys', ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', '⌫'].map((d) =>
      d ? h('button.pin-key', { type: 'button', onclick: () => press(d) }, d) : h('span'))));
}

export async function switchTo(p, onDone) {
  const go = async (payload) => {
    await post(`/api/profiles/${p.id}/switch`, payload);
    toast(`Bonjour ${p.display_name} !`, 'ok');
    onDone();
  };
  if (p.current || p.needs === 'none') {
    try { await go({}); } catch (e) { toast(e.message, 'bad'); }
    return;
  }
  if (p.needs === 'pin') {
    const err = h('p.form-error.center');
    await modal(`Code PIN de ${p.display_name}`, h('div.center', avatar(p, 64), pinPad(async (pin, reset) => {
      try {
        await go({ pin });
        document.querySelector('.modal-overlay')?.remove();
      } catch (e) { err.textContent = e.message; reset(); }
    }), err), []);
    return;
  }
  const pass = h('input', { type: 'password', autocomplete: 'current-password' });
  const err = h('p.form-error');
  await modal(`Mot de passe de ${p.display_name}`, h('div', field('Mot de passe', pass), err), [
    { label: 'Annuler', value: null },
    { label: 'Continuer', primary: true, handler: async () => {
      try { await go({ password: pass.value }); return true; } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

export async function renderProfiles(root, onDone) {
  const list = await get('/api/profiles');
  if (list.length < 2) { onDone(); return; }
  clear(root, h('div.profiles-page',
    h('div.auth-brand', h('span.logo-mark.big')),
    h('h1.profiles-title', 'Qui regarde ?'),
    h('div.profiles-grid', list.map((p) => h('button.profile-tile', { class: p.current ? 'profile-tile current' : 'profile-tile',
      onclick: () => switchTo(p, onDone) },
    avatar(p, 112),
    h('span.profile-name', p.display_name),
    p.needs === 'pin' ? h('span.profile-lock', '🔒') : null,
    p.max_age != null ? h('span.profile-kid', p.max_age <= 10 ? 'Enfant' : `${p.max_age} ans max`) : null))),
    h('p.muted.small.center', 'Les profils protégés demandent leur code PIN.')));
}
