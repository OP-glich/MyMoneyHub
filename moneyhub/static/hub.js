'use strict';

function openPanel(id) {
  const panel = document.getElementById(id);
  if (panel && panel.tagName === 'DETAILS') panel.open = true;
}
document.querySelectorAll('[data-open]').forEach(link => link.addEventListener('click', () => openPanel(link.dataset.open)));
if (location.hash) openPanel(decodeURIComponent(location.hash.slice(1)));

document.querySelectorAll('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));

document.querySelectorAll('[data-copy-message]').forEach(button => button.addEventListener('click', async () => {
  const target = button.dataset || {};
  const message = document.getElementById(target.copyTarget || 'whatsapp-message');
  const status = document.getElementById(target.copyStatus || 'copy-status');
  try {
    await navigator.clipboard.writeText(message.value);
    status.textContent = 'Message copied. Paste it into WhatsApp when you’re ready.';
  } catch (_) {
    const details = message.closest('details');
    if (details) details.open = true;
    message.focus();
    message.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (_) { /* Show manual fallback. */ }
    status.textContent = copied ? 'Message copied. Paste it into WhatsApp when you’re ready.' : 'Select the message above and press Ctrl+C to copy it.';
  }
}));

// Ask the local server for exact Decimal calculations; never calculate money
// using JavaScript floating-point numbers.
document.querySelectorAll('.loan-form').forEach(form => {
  let timer;
  let pending;
  const updatePreview = () => {
    clearTimeout(timer);
    if (pending) pending.abort();
    timer = setTimeout(async () => {
      pending = new AbortController();
      const values = new FormData(form);
      const query = new URLSearchParams();
      ['amount', 'rate', 'basis', 'date', 'due'].forEach(name => query.set(name, values.get(name) || ''));
      const error = form.querySelector('[data-preview-error]');
      if (!values.get('amount') || !values.get('due') || !values.get('date')) {
        form.querySelector('[data-preview-interest]').textContent = '—';
        form.querySelector('[data-preview-total]').textContent = '—';
        error.textContent = 'Enter the amount and both dates, then calculate the preview.';
        return;
      }
      try {
        const response = await fetch('/loan-preview?' + query, {signal: pending.signal});
        const result = await response.json();
        form.querySelector('[data-preview-interest]').textContent = response.ok ? result.interest : '—';
        form.querySelector('[data-preview-total]').textContent = response.ok ? result.total : '—';
        error.textContent = response.ok ? 'Based on the agreed loan dates. The saved receipt will show the same total.' : result.error;
      } catch (exception) {
        if (exception.name !== 'AbortError') error.textContent = 'Automatic preview is unavailable. Use Calculate Preview below; nothing will be saved.';
      }
    }, 180);
  };
  form.addEventListener('input', updatePreview);
  form.addEventListener('change', updatePreview);
  updatePreview();
});

document.addEventListener('submit', event => {
  const form = event.target;
  if (form.method.toLowerCase() !== 'post' || form.action.endsWith('/backup')) return;
  if (form.dataset.submitting === 'true') { event.preventDefault(); return; }
  form.dataset.submitting = 'true';
  const button = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
  if (button) {
    button.dataset.originalLabel = button.textContent;
    // Keep successful submitter controls in the native form submission.
    button.setAttribute('aria-disabled', 'true');
    button.textContent = 'Saving…';
  }
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('button[data-original-label]').forEach(button => {
    button.disabled = false;
    button.removeAttribute('aria-disabled');
    button.textContent = button.dataset.originalLabel;
  });
  document.querySelectorAll('form[data-submitting]').forEach(form => { form.dataset.submitting = ''; });
});
const feedback = document.querySelectorAll('[data-focus-feedback]')[0];
if (feedback) feedback.focus();
