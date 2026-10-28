import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { gridCanvas, readPalette } from "../lib/draw";
import type { OccGrid } from "../lib/grid";
import type { Twin, XY } from "../types";

interface Props {
  twin: Twin;
  grid: OccGrid;
  foot: XY[];
  robotHeight: number;
  state: { robot: [number, number, number, number]; peds: XY[] } | null;
  height: number;
}

/** 3D view of the twin: occupancy floor texture, extruded primitives, parametric robot (no vendor meshes). Z up. */
export default function Scene3D({ twin, grid, foot, robotHeight, state, height }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const live = useRef<{ robot: THREE.Mesh; peds: THREE.Group; pedGeo: THREE.CylinderGeometry; pedMat: THREE.Material; render: () => void } | null>(null);

  useEffect(() => {
    const el = host.current!;
    const pal = readPalette();
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    renderer.setSize(el.clientWidth, height);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(pal.bg);
    const W = grid.width * grid.res;
    const H = grid.height * grid.res;
    const cx = grid.origin[0] + W / 2;
    const cy = grid.origin[1] + H / 2;
    const camera = new THREE.PerspectiveCamera(45, el.clientWidth / height, 0.05, 200);
    camera.up.set(0, 0, 1);
    camera.position.set(cx - W * 0.2, cy - H * 0.95, Math.max(W, H) * 0.75);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(cx, cy, 0);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(cx + 4, cy - 6, 10);
    scene.add(sun);

    const tex = new THREE.CanvasTexture(gridCanvas(grid, pal));
    tex.magFilter = THREE.NearestFilter;
    tex.colorSpace = THREE.SRGBColorSpace;
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, H), new THREE.MeshLambertMaterial({ map: tex }));
    floor.position.set(cx, cy, 0);
    scene.add(floor);

    const movMat = new THREE.MeshLambertMaterial({ color: pal.accent, transparent: true, opacity: 0.75 });
    const fixMat = new THREE.MeshLambertMaterial({ color: 0x8892a0 });
    for (const p of twin.primitives ?? []) {
      const h = Math.max(0.02, p.size[2]);
      const geo = p.type === "cylinder" ? new THREE.CylinderGeometry(p.size[0] / 2, p.size[0] / 2, h, 24).rotateX(Math.PI / 2) : new THREE.BoxGeometry(p.size[0], p.size[1], h);
      const m = new THREE.Mesh(geo, p.movable ? movMat : fixMat);
      m.position.set(p.pose.xyz[0], p.pose.xyz[1], h / 2);
      m.rotation.z = p.pose.yaw ?? 0;
      scene.add(m);
    }
    const koMat = new THREE.MeshBasicMaterial({ color: pal.red, transparent: true, opacity: 0.25, side: THREE.DoubleSide });
    for (const k of twin.keepout_zones ?? []) {
      if (k.polygon.length < 3) continue;
      const shape = new THREE.Shape(k.polygon.map(([x, y]) => new THREE.Vector2(x, y)));
      const m = new THREE.Mesh(new THREE.ShapeGeometry(shape), koMat);
      m.position.z = 0.005;
      scene.add(m);
    }
    const wpMat = new THREE.MeshLambertMaterial({ color: pal.violet });
    for (const w of twin.waypoints ?? []) {
      const m = new THREE.Mesh(new THREE.ConeGeometry(0.08, 0.25, 12).rotateX(-Math.PI / 2), wpMat);
      m.position.set(w.xy[0], w.xy[1], 0.13);
      scene.add(m);
    }
    const shape = new THREE.Shape(foot.map(([x, y]) => new THREE.Vector2(x, y)));
    const robot = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: robotHeight, bevelEnabled: false }), new THREE.MeshLambertMaterial({ color: pal.accent }));
    scene.add(robot);
    const peds = new THREE.Group();
    scene.add(peds);
    const pedGeo = new THREE.CylinderGeometry(0.2, 0.2, 1.7, 14).rotateX(Math.PI / 2);
    const pedMat = new THREE.MeshLambertMaterial({ color: 0xd9a066 });

    let raf = 0;
    const render = () => renderer.render(scene, camera);
    const loop = () => {
      controls.update();
      render();
      raf = requestAnimationFrame(loop);
    };
    loop();
    live.current = { robot, peds, pedGeo, pedMat, render };
    const ro = new ResizeObserver(() => {
      renderer.setSize(el.clientWidth, height);
      camera.aspect = el.clientWidth / height;
      camera.updateProjectionMatrix();
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh) o.geometry.dispose();
      });
      tex.dispose();
      el.removeChild(renderer.domElement);
      live.current = null;
    };
  }, [twin, grid, foot, robotHeight, height]);

  useEffect(() => {
    const l = live.current;
    if (!l || !state) return;
    const [x, y, yaw] = state.robot;
    l.robot.position.set(x, y, 0);
    l.robot.rotation.z = yaw;
    while (l.peds.children.length > state.peds.length) l.peds.remove(l.peds.children[l.peds.children.length - 1]);
    while (l.peds.children.length < state.peds.length) l.peds.add(new THREE.Mesh(l.pedGeo, l.pedMat));
    state.peds.forEach(([px, py], i) => l.peds.children[i].position.set(px, py, 0.85));
  }, [state]);

  return <div ref={host} style={{ height, width: "100%" }} />;
}
