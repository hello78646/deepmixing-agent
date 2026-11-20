'use strict';
(() => {
  const opening = document.getElementById('workbench-opening');
  if (!opening?.showModal) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cues = [...opening.querySelectorAll('[data-opening-at]')];
  const steps = [...opening.querySelectorAll('.opening-steps i')];
  const cueTimers = [];
  const duration = Number(opening.dataset.duration) || 4300;
  const fadeDuration = reduced ? 180 : 520;
  let fadeTimer;

  function showCue(activeIndex) {
    cues.forEach((cue, index) => {
      cue.hidden = index !== activeIndex;
      steps[index]?.classList.toggle('is-current', index === activeIndex);
    });
  }
  function revealWorkbench() {
    if (opening.classList.contains('is-leaving')) return;
    opening.classList.add('is-leaving');
    opening.dispatchEvent(new Event('workbench-opening-reveal'));
  }
  function closeOpening(skip = false) {
    clearTimeout(fadeTimer);
    cueTimers.forEach(clearTimeout);
    revealWorkbench();
    if (opening.open) opening.close();
    if (skip) document.getElementById(opening.dataset.focusTarget || 'shell-mode-smart')?.focus({preventScroll:true});
  }

  document.getElementById('workbench-opening-skip')?.addEventListener('click', () => closeOpening(true));
  opening.addEventListener('cancel', event => { event.preventDefault(); closeOpening(true); });
  opening.showModal();
  showCue(0);
  cues.slice(1).forEach((cue, index) => {
    cueTimers.push(setTimeout(() => showCue(index + 1), Number(cue.dataset.openingAt)));
  });
  fadeTimer = setTimeout(() => {
    revealWorkbench();
    setTimeout(() => closeOpening(false), fadeDuration);
  }, duration - fadeDuration);
})();
