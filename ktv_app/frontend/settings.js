import { $, api, notify } from './ui.js';

export async function initSettings({ onHealth }) {
  const dialog = $('settingsDialog');
  $('openSettings').onclick = () => dialog.showModal();
  $('closeSettings').onclick = () => dialog.close();
  try {
    onHealth(await api('/health'));
  } catch (error) {
    notify(error.message);
  }
}
