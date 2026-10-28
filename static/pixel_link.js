/* Open pixel editor with the active VoiceDrama project and scene preselected. */
document.addEventListener('DOMContentLoaded', () => {
  const link = document.getElementById('pixelStudioLink');
  if (!link) return;
  link.addEventListener('click', (event) => {
    try {
      if (typeof state !== 'undefined' && state?.id) {
        const args = new URLSearchParams({project: state.id});
        if (typeof selectedScene !== 'undefined' && selectedScene) args.set('scene', selectedScene);
        link.href = '/pixel/?' + args.toString();
      }
    } catch (_) { /* Fallback link remains usable. */ }
  });
});
