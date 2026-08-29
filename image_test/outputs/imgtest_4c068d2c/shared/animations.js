// =============================================================
// HyperFrames Shared Animation Helpers
// All animations use GSAP timelines — seekable by HyperFrames
// DO NOT use setTimeout, setInterval, or requestAnimationFrame
// =============================================================

window.HFAnimations = {
  // Fade in an element
  fadeIn: function(el, delay, duration) {
    delay = delay || 0;
    duration = duration || 0.6;
    return gsap.fromTo(el, { opacity: 0 }, { opacity: 1, duration: duration, delay: delay, ease: 'power2.out' });
  },

  // Slide up and fade in
  slideUp: function(el, delay, duration) {
    delay = delay || 0;
    duration = duration || 0.5;
    return gsap.fromTo(el,
      { opacity: 0, y: 40 },
      { opacity: 1, y: 0, duration: duration, delay: delay, ease: 'power3.out' }
    );
  },

  // Scale pop in (spring-like with back easing)
  scaleIn: function(el, delay, duration) {
    delay = delay || 0;
    duration = duration || 0.4;
    return gsap.fromTo(el,
      { opacity: 0, scale: 0.4 },
      { opacity: 1, scale: 1, duration: duration, delay: delay, ease: 'back.out(1.7)' }
    );
  },

  // Slide from left
  slideLeft: function(el, delay, duration) {
    delay = delay || 0;
    duration = duration || 0.5;
    return gsap.fromTo(el,
      { opacity: 0, x: -60 },
      { opacity: 1, x: 0, duration: duration, delay: delay, ease: 'power2.out' }
    );
  },

  // Slide from right
  slideRight: function(el, delay, duration) {
    delay = delay || 0;
    duration = duration || 0.5;
    return gsap.fromTo(el,
      { opacity: 0, x: 60 },
      { opacity: 1, x: 0, duration: duration, delay: delay, ease: 'power2.out' }
    );
  },

  // Staggered fade-in for multiple elements
  staggerFadeIn: function(selector, stagger, delay) {
    stagger = stagger || 0.15;
    delay = delay || 0;
    return gsap.fromTo(selector,
      { opacity: 0, y: 20 },
      { opacity: 1, y: 0, duration: 0.5, stagger: stagger, delay: delay, ease: 'power2.out' }
    );
  },

  // Staggered scale-in for card grids
  staggerScaleIn: function(selector, stagger, delay) {
    stagger = stagger || 0.1;
    delay = delay || 0;
    return gsap.fromTo(selector,
      { opacity: 0, scale: 0.6 },
      { opacity: 1, scale: 1, duration: 0.4, stagger: stagger, delay: delay, ease: 'back.out(1.5)' }
    );
  },

  // Draw SVG stroke from 0% to 100%
  drawSvgStroke: function(el, delay, duration) {
    delay = delay || 0;
    duration = duration || 1.0;
    const length = el.getTotalLength ? el.getTotalLength() : 1000;
    gsap.set(el, { strokeDasharray: length, strokeDashoffset: length });
    return gsap.to(el, { strokeDashoffset: 0, duration: duration, delay: delay, ease: 'power2.inOut' });
  },

  // Floating idle bob (use in gsap.ticker or on a repeating timeline)
  createFloatTimeline: function(el, amplitude, period) {
    amplitude = amplitude || 6;
    period = period || 2;
    const tl = gsap.timeline({ repeat: 50, yoyo: true });
    tl.to(el, { y: amplitude, duration: period / 2, ease: 'sine.inOut' });
    return tl;
  },

  // Pulse glow effect (border/box-shadow)
  pulse: function(el, color, delay) {
    delay = delay || 0;
    const tl = gsap.timeline({ repeat: 50, yoyo: true, delay: delay });
    tl.to(el, {
      boxShadow: `0 0 32px rgba(${color}, 0.6), 0 0 64px rgba(${color}, 0.2)`,
      duration: 1.2,
      ease: 'sine.inOut'
    });
    return tl;
  },

  // Cascade reveal: items appear one-by-one with a line connecting them
  cascadeReveal: function(items, delay) {
    delay = delay || 0;
    const tl = gsap.timeline({ delay: delay });
    items.forEach(function(item, idx) {
      tl.fromTo(item,
        { opacity: 0, x: -30 },
        { opacity: 1, x: 0, duration: 0.4, ease: 'power2.out' },
        idx * 0.2
      );
    });
    return tl;
  },
};
