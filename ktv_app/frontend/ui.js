export const $ = (id) => document.getElementById(id);

function notify(text) {
  $('notice').textContent = text;
  $('notice').style.display = 'block';
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => ($('notice').style.display = 'none'), 7000);
}
async function api(path, method = 'GET', body) {
  const opts = {
    method,
  };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers = {
      'Content-Type': 'application/json',
    };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch('/api' + path, opts);
  const data = await r.json();
  if (!r.ok)
    throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}

function handler(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      notify(e.message);
    }
  };
}

export { notify, api, handler };
