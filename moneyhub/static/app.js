'use strict';
// Normal HTML forms remain usable without JavaScript. Suppress accidental
// double-clicks; the database operation key remains the authoritative defense.
document.addEventListener('submit', event => {
  const form = event.target;
  if (form.method.toLowerCase() !== 'post' || form.action.endsWith('/backup')) return;
  const button = form.querySelector('button');
  if (button) { button.disabled = true; button.textContent = 'Saving…'; }
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('button:disabled').forEach(button => {
    button.disabled = false;
    button.textContent = 'Submit';
  });
});
