// =============================================================
// HyperFrames Shared Theme System
// Mirrors: remotion_test_app/src/themeConfig.json exactly
// =============================================================

window.HFThemes = {
  Science: {
    background: 'linear-gradient(135deg, #021a14 0%, #0a3526 100%)',
    accentColor: '#10b981',
    accentRgb: '16, 185, 129',
    textColor: '#f8fafc',
    cardBackground: 'rgba(16, 185, 129, 0.06)',
    cardBorder: '1px solid rgba(16, 185, 129, 0.2)',
    fontFamily: "'Outfit', system-ui, sans-serif",
    stiffness: 100,
    damping: 15,
  },
  Math: {
    background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)',
    accentColor: '#38bdf8',
    accentRgb: '56, 189, 248',
    textColor: '#f8fafc',
    cardBackground: 'rgba(56, 189, 248, 0.05)',
    cardBorder: '1px solid rgba(56, 189, 248, 0.2)',
    fontFamily: "'Space Grotesk', system-ui, sans-serif",
    stiffness: 140,
    damping: 12,
  },
  History: {
    background: 'linear-gradient(135deg, #180c05 0%, #2b180a 100%)',
    accentColor: '#f59e0b',
    accentRgb: '245, 158, 11',
    textColor: '#fef3c7',
    cardBackground: 'rgba(245, 158, 11, 0.06)',
    cardBorder: '1px solid rgba(245, 158, 11, 0.2)',
    fontFamily: "'Cinzel', 'Playfair Display', serif",
    stiffness: 80,
    damping: 18,
  },
  Civics: {
    background: 'linear-gradient(135deg, #090d16 0%, #151030 100%)',
    accentColor: '#6366f1',
    accentRgb: '99, 102, 241',
    textColor: '#f8fafc',
    cardBackground: 'rgba(99, 102, 241, 0.06)',
    cardBorder: '1px solid rgba(99, 102, 241, 0.2)',
    fontFamily: "'Inter', system-ui, sans-serif",
    stiffness: 110,
    damping: 14,
  },
  General: {
    background: 'linear-gradient(135deg, #090d16 0%, #151030 100%)',
    accentColor: '#6366f1',
    accentRgb: '99, 102, 241',
    textColor: '#f8fafc',
    cardBackground: 'rgba(99, 102, 241, 0.06)',
    cardBorder: '1px solid rgba(99, 102, 241, 0.2)',
    fontFamily: "'Inter', system-ui, sans-serif",
    stiffness: 110,
    damping: 14,
  },
  // Remotion shorthand aliases
  indigo: {
    background: 'linear-gradient(135deg, #090d16 0%, #151030 100%)',
    accentColor: '#6366f1',
    accentRgb: '99, 102, 241',
    textColor: '#f8fafc',
    cardBackground: 'rgba(99, 102, 241, 0.06)',
    cardBorder: '1px solid rgba(99, 102, 241, 0.2)',
    fontFamily: "'Inter', system-ui, sans-serif",
    stiffness: 110,
    damping: 14,
  },
  gold: {
    background: 'linear-gradient(135deg, #180c05 0%, #2b180a 100%)',
    accentColor: '#f59e0b',
    accentRgb: '245, 158, 11',
    textColor: '#fef3c7',
    cardBackground: 'rgba(245, 158, 11, 0.06)',
    cardBorder: '1px solid rgba(245, 158, 11, 0.2)',
    fontFamily: "'Cinzel', 'Playfair Display', serif",
    stiffness: 80,
    damping: 18,
  },
  emerald: {
    background: 'linear-gradient(135deg, #021a14 0%, #0a3526 100%)',
    accentColor: '#10b981',
    accentRgb: '16, 185, 129',
    textColor: '#f8fafc',
    cardBackground: 'rgba(16, 185, 129, 0.06)',
    cardBorder: '1px solid rgba(16, 185, 129, 0.2)',
    fontFamily: "'Outfit', system-ui, sans-serif",
    stiffness: 100,
    damping: 15,
  },
  rose: {
    background: 'linear-gradient(135deg, #1a0209 0%, #2b0a14 100%)',
    accentColor: '#f43f5e',
    accentRgb: '244, 63, 94',
    textColor: '#f8fafc',
    cardBackground: 'rgba(244, 63, 94, 0.06)',
    cardBorder: '1px solid rgba(244, 63, 94, 0.2)',
    fontFamily: "'Outfit', system-ui, sans-serif",
    stiffness: 100,
    damping: 15,
  },
};

window.getTheme = function(name) {
  return window.HFThemes[name] || window.HFThemes['General'];
};

// Apply background and font to body using active theme
window.applyTheme = function(themeName) {
  const t = window.getTheme(themeName);
  document.body.style.background = t.background;
  document.body.style.fontFamily = t.fontFamily;
  document.body.style.color = t.textColor;
};
