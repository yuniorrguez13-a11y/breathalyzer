/* Breathalyzer site: theme toggle, small-screen menu, reveal on scroll. No trackers, no network requests. */
(function () {
  'use strict';
  var root = document.documentElement;

  /* ---- Theme (dark by default; the choice is kept in this browser only) ---- */
  var themeBtn = document.querySelector('.theme-toggle');
  var themeText = themeBtn && themeBtn.querySelector('.theme-toggle-text');
  var themeMeta = document.querySelector('meta[name="theme-color"]');

  function save(theme) {
    try { localStorage.setItem('blz-theme', theme); } catch (e) { /* private mode: fine */ }
  }
  function applyTheme(theme) {
    root.setAttribute('data-theme', theme);
    var next = theme === 'dark' ? 'light' : 'dark';
    if (themeText) themeText.textContent = next === 'light' ? 'Light' : 'Dark';
    if (themeBtn) themeBtn.setAttribute('aria-label', 'Switch to ' + next + ' mode');
    if (themeMeta) themeMeta.setAttribute('content', theme === 'dark' ? '#0b1013' : '#eef3f5');
  }
  if (themeBtn) {
    themeBtn.hidden = false;
    applyTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    themeBtn.addEventListener('click', function () {
      var theme = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      applyTheme(theme);
      save(theme);
    });
  }

  /* ---- Small-screen menu ---- */
  var navBtn = document.querySelector('.nav-toggle');
  var nav = document.getElementById('site-nav');
  function setNav(open) {
    nav.classList.toggle('is-open', open);
    navBtn.setAttribute('aria-expanded', String(open));
  }
  if (navBtn && nav) {
    navBtn.hidden = false;
    navBtn.addEventListener('click', function () {
      setNav(navBtn.getAttribute('aria-expanded') !== 'true');
    });
    nav.addEventListener('click', function (e) {
      if (e.target.closest('a')) setNav(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && navBtn.getAttribute('aria-expanded') === 'true') {
        setNav(false);
        navBtn.focus();
      }
    });
  }

  /* ---- Reveal on scroll (skipped entirely when the reader prefers less motion) ---- */
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce || !('IntersectionObserver' in window)) return;

  root.classList.add('motion');
  var items = document.querySelectorAll('[data-reveal]');
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-in');
        io.unobserve(entry.target);
      }
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
  items.forEach(function (el) { io.observe(el); });

  /* A section reached by a link is shown at once, so nothing is still fading in when you land on it. */
  function revealTarget() {
    var id = decodeURIComponent(location.hash.slice(1));
    var target = id && document.getElementById(id);
    if (!target) return;
    if (target.matches('[data-reveal]')) target.classList.add('is-in');
    target.querySelectorAll('[data-reveal]').forEach(function (el) { el.classList.add('is-in'); });
  }
  window.addEventListener('hashchange', revealTarget);
  revealTarget();
})();
