const Scene = require('../engine/scene/Scene');
const Renderer = require('../engine/renderer/Renderer');

/**
 * ImageScene.js
 * Template orchestrator delegating layout rendering entirely to the engine Renderer.
 */
module.exports = {
  render: (sId, data, storyboard) => {
    const sceneJson = storyboard.scenes.find(s => s.scene_no === sId);
    const scene = Scene.deserialize(sceneJson);
    return Renderer.renderScene(scene);
  },
  animate: (sId, data, theme, sceneDuration) => {
    const animStyle = data.animation_style || 'simple_zoom';
    const zoomTargets = data.zoom_targets || [];
    // Entrance fade/scale-in tween runs first, 0 -> ENTRANCE_DURATION, then
    // the zoom tween(s) take over. The entrance tween MUST have an explicit
    // position (0) on the sceneTl.fromTo() call below - without one, GSAP
    // auto-sequences it to start after whatever was added to sceneTl before
    // it (the camera-pan tween, which runs the full scene duration), so the
    // fade-in didn't begin until seconds before the scene ended. Confirmed
    // via GSAP's own timeline introspection (gsap.getChildren) on a real
    // compiled lesson: the entrance tween's startTime was ~12.3 in a ~12.9s
    // scene, not 0 - by the time it would have finished, the scene was
    // already transitioning out, so the image never appeared to show at all
    // even though narration/camera/everything else played correctly.
    const ENTRANCE_DURATION = 0.6;
    const totalDuration = sceneDuration || 5.0;
    const zoomDuration = Math.max(0.1, totalDuration - ENTRANCE_DURATION);

    let zoomLogic = '';
    if (animStyle === 'simple_zoom') {
      zoomLogic = `sceneTl.to('#img-el-${sId}', { scale: 1.15, duration: ${zoomDuration}, ease: 'none' }, ${ENTRANCE_DURATION});`;
    } else if (zoomTargets.length > 0) {
      zoomTargets.forEach(target => {
        const timeAt = Math.max((target.at_percent / 100) * totalDuration, ENTRANCE_DURATION);
        zoomLogic += `
          sceneTl.to('#img-el-${sId}', {
            scale: ${target.scale || 1.0},
            x: ${(50 - target.x) * 5} + 'px',
            y: ${(50 - target.y) * 5} + 'px',
            duration: 1.0,
            ease: 'power2.inOut'
          }, ${timeAt});
        `;
      });
    }

    return `
      sceneTl.fromTo('#img-el-${sId}', { opacity: 0, scale: 0.8 }, { opacity: 1, scale: 1, duration: ${ENTRANCE_DURATION}, ease: 'power3.out' }, 0);
      ${zoomLogic}
    `;
  }
};
