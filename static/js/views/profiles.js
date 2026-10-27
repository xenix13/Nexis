// « Qui regarde ? » : changement de profil du foyer, avec code PIN.
import { get, post } from '../api.js';
import { avatar, clear, field, h, modal, toast } from '../ui.js';
import { tr } from '../i18n.js';

function pinPad(onSubmit, length = 4) {
  const input = h('input.pin-input', { type: 'password', inputmode: 'numeric', autocomplete: 'off', maxlength: 8,
    pattern: '[0-9]*', 'aria-label': tr('Code PIN') });
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

// Compte protégé par la double authentification : le code est demandé en plus du PIN ou du mot de passe
async function askCode(p) {
  const code = h('input', { autocomplete: 'one-time-code', autocapitalize: 'none', maxlength: 9, placeholder: '123456' });
  return modal(tr('Double authentification de {display_name}', { display_name: p.display_name }), h('div',
    h('p.muted.small', tr('Code à 6 chiffres de l’application d’authentification, ou un code de secours.')),
    field(tr('Code'), code)), [
    { label: tr('Annuler'), value: null },
    { label: tr('Valider'), primary: true, handler: () => code.value.trim() || false },
  ]);
}

export async function switchTo(p, onDone) {
  const go = async (payload) => {
    if (p.needs_code && !p.current) {
      const code = await askCode(p);
      if (!code) throw new Error(tr('Changement de profil annulé'));
      payload = { ...payload, code };
    }
    await post(`/api/profiles/${p.id}/switch`, payload);
    toast(tr('Bonjour {display_name} !', { display_name: p.display_name }), 'ok');
    onDone();
  };
  if (p.current || p.needs === 'none') {
    try { await go({}); } catch (e) { toast(e.message, 'bad'); }
    return;
  }
  if (p.needs === 'pin') {
    const err = h('p.form-error.center');
    await modal(tr('Code PIN de {display_name}', { display_name: p.display_name }), h('div.center', avatar(p, 64), pinPad(async (pin, reset) => {
      try {
        if (p.needs_code) err.closest('.modal').querySelector('.modal-head .icon-btn').click(); // place au code
        await go({ pin });
        document.querySelector('.modal-overlay')?.remove();
      } catch (e) {
        if (p.needs_code) { toast(e.message, 'bad'); return; }
        err.textContent = e.message; reset();
      }
    }), err), []);
    return;
  }
  const pass = h('input', { type: 'password', autocomplete: 'current-password' });
  const err = h('p.form-error');
  await modal(tr('Mot de passe de {display_name}', { display_name: p.display_name }), h('div', field(tr('Mot de passe'), pass), err), [
    { label: tr('Annuler'), value: null },
    { label: tr('Continuer'), primary: true, handler: async () => {
      try { await go({ password: pass.value }); return true; } catch (e) { err.textContent = e.message; return false; }
    } },
  ]);
}

export async function renderProfiles(root, onDone) {
  const list = await get('/api/profiles');
  if (list.length < 2) { onDone(); return; }
  clear(root, h('div.profiles-page',
    h('div.auth-brand', h('span.logo-mark.big')),
    h('h1.profiles-title', tr('Qui regarde ?')),
    h('div.profiles-grid', list.map((p) => h('button.profile-tile', { class: p.current ? 'profile-tile current' : 'profile-tile',
      onclick: () => switchTo(p, onDone) },
    avatar(p, 112),
    h('span.profile-name', p.display_name),
    p.needs === 'pin' ? h('span.profile-lock', '🔒') : null,
    p.max_age != null ? h('span.profile-kid', p.max_age <= 10 ? tr('Enfant') : tr('{max_age} ans max', { max_age: p.max_age })) : null))),
    h('p.muted.small.center', tr('Les profils protégés demandent leur code PIN.'))));
}
