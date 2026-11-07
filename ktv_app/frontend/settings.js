// Settings dialog: the download folder is saved on the server, so exports land there.
import { $, api, notify, handler } from './ui.js';

function showStatus(downloadDir, saved = false) {
  $('settingsStatus').textContent = downloadDir
    ? saved
      ? '已保存，导出会直接写入该目录'
      : '导出会直接写入该目录'
    : saved
      ? '已清空，成片只保留在项目文件夹'
      : '未设置，成片只保留在项目文件夹';
}

export async function initSettings({ onHealth }) {
  const dialog = $('settingsDialog');
  const load = handler(async () => {
    const values = await api('/settings');
    $('downloadDir').value = values.download_dir || '';
    showStatus(values.download_dir);
  });
  $('openSettings').onclick = () => {
    dialog.showModal();
    load();
  };
  $('closeSettings').onclick = () => dialog.close();
  $('saveSettings').onclick = handler(async () => {
    const saved = await api('/settings', 'PUT', {
      download_dir: $('downloadDir').value,
    });
    $('downloadDir').value = saved.download_dir;
    showStatus(saved.download_dir, true);
    notify('设置已保存');
  });
  try {
    onHealth(await api('/health'));
  } catch (error) {
    notify(error.message);
  }
}
