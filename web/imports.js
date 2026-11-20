'use strict';
// Extends the same local asset pipeline; finding a webpage is not audio import.
async function searchLibrary() {
  const query = $('library-query').value.trim();
  setBusy(true); status('正在查找本地素材：' + (query || '全部素材'));
  try {
    const result = await api('/api/library/search?q=' + encodeURIComponent(query));
    const root = $('library-results'); root.replaceChildren();
    root.append(node('p', result.tracks.length ? `找到${result.tracks.length}份本地素材` : '本地还没有这首歌。可先前往音乐平台确认版本，再导入可用文件。', 'hint'));
    for (const track of result.tracks) {
      const button = node('button', track.name + ' · ' + seconds(track.duration), 'library-choice');
      button.dataset.importAsset = track.id;
      button.addEventListener('click', () => {
        const target = $('import-target').value;
        clearProject(); fillSources({ [target]: track.id });
        status('已将本地素材“' + track.name + '”放入' + target.toUpperCase() + '轨。');
      });
      root.append(button);
    }
    const links = $('platform-links'); links.replaceChildren();
    if (query) for (const platform of result.platform_search_links) {
      const link = node('a', '在' + platform.name + '搜索 ↗');
      link.href = platform.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link);
    }
    status(result.message);
  } catch (error) { status(error.message, true); }
  finally { setBusy(false); }
}
async function importMediaLink() {
  const target = $('import-target').value;
  const text = $('import-link').value.trim();
  if (!text) return status('请粘贴一条音视频文件链接，或平台分享文案。', true);
  setBusy(true); status('正在识别来源；直链文件会下载到本机后解码，平台网页不会冒充音频。');
  $('link-result').textContent = '';
  try {
    const result = await api('/api/imports/link', { text });
    $('link-result').textContent = result.message;
    if (result.audio_imported) {
      tracks = await api('/api/tracks');
      clearProject(); fillSources({ [target]: result.track.id });
      status('已实际导入“' + result.track.name + '”，放入' + target.toUpperCase() + '轨。');
    } else {
      status(result.message);
    }
  } catch (error) { $('link-result').textContent = error.message; status(error.message, true); }
  finally { setBusy(false); }
}
$('library-search').addEventListener('click', searchLibrary);
$('library-query').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !busy) searchLibrary();
});
$('link-import').addEventListener('click', importMediaLink);
$('file-video').addEventListener('change', async () => {
  const file = $('file-video').files[0];
  if (file) await upload($('import-target').value, file);
  $('file-video').value = '';
});
