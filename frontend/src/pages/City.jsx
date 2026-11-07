import React, { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { api } from '../api.js'
import { Spinner, useFetch, useRepo } from '../components/common.jsx'
import { colorFor } from '../components/GraphCanvas.jsx'

const METRICS = [['module', 'by top-level folder'], ['complexity', 'by complexity'], ['commits', 'by churn (commits)'], ['loc', 'by size'], ['test', 'tests vs code']]
const ramp = (t) => new THREE.Color().setHSL(0.38 - 0.38 * Math.min(1, Math.max(0, t)), 0.62, 0.5)

export default function City() {
  const { id } = useRepo()
  const nav = useNavigate()
  const { data, loading } = useFetch(() => api.get(`/api/repos/${id}/city`), [id])
  const [metric, setMetric] = useState('module')
  const mount = useRef(), tip = useRef(), api3 = useRef({})
  useEffect(() => {
    if (!data || !mount.current) return
    const el = mount.current
    const W = () => el.clientWidth, H = () => el.clientHeight
    const scene = new THREE.Scene()
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches
    scene.background = new THREE.Color(dark ? 0x0b1b24 : 0xf3f6f4)
    const cam = new THREE.PerspectiveCamera(50, W() / H(), 0.1, 2000)
    cam.position.set(data.size * 0.9, data.size * 0.8, data.size * 1.3)
    const r = new THREE.WebGLRenderer({ antialias: true }); r.setSize(W(), H()); r.setPixelRatio(Math.min(2, devicePixelRatio)); el.appendChild(r.domElement)
    const ctl = new OrbitControls(cam, r.domElement); ctl.target.set(data.size / 2, 0, data.size / 2); ctl.enableDamping = true
    scene.add(new THREE.AmbientLight(0xffffff, 0.75)); const sun = new THREE.DirectionalLight(0xffffff, 0.9); sun.position.set(60, 120, 40); scene.add(sun)
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(data.size * 1.4, data.size * 1.4), new THREE.MeshLambertMaterial({ color: dark ? 0x10252f : 0xe3ebe8 }))
    ground.rotation.x = -Math.PI / 2; ground.position.set(data.size / 2, -0.05, data.size / 2); scene.add(ground)
    data.districts.forEach((d) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(d.w, d.d), new THREE.MeshLambertMaterial({ color: dark ? 0x16323f : 0xd5e1dd, transparent: true, opacity: 0.8 }))
      m.rotation.x = -Math.PI / 2; m.position.set(d.x + d.w / 2, 0.02 + d.depth * 0.02, d.z + d.d / 2); scene.add(m)
    })
    const maxOf = (k) => Math.max(1, ...data.buildings.map((b) => b[k] || 0))
    const mx = { complexity: maxOf('complexity'), commits: maxOf('commits'), loc: maxOf('loc') }
    const meshes = data.buildings.map((b) => {
      let c
      if (metric === 'module') c = new THREE.Color(colorFor(b.top)); else if (metric === 'test') c = new THREE.Color(b.is_test ? '#5B9BD5' : '#E0A526'); else c = ramp((b[metric] || 0) / mx[metric])
      const h = metric === 'loc' ? 1 + (b.loc / mx.loc) * 16 : b.height
      const m = new THREE.Mesh(new THREE.BoxGeometry(Math.max(0.4, b.w), h, Math.max(0.4, b.d)), new THREE.MeshLambertMaterial({ color: c }))
      m.position.set(b.x + b.w / 2, h / 2 + 0.05, b.z + b.d / 2); m.userData = b; scene.add(m); return m
    })
    const ray = new THREE.Raycaster(), mouse = new THREE.Vector2()
    let hover = null
    const move = (e) => {
      const rc = r.domElement.getBoundingClientRect(); mouse.set(((e.clientX - rc.left) / rc.width) * 2 - 1, -((e.clientY - rc.top) / rc.height) * 2 + 1)
      ray.setFromCamera(mouse, cam); const hit = ray.intersectObjects(meshes)[0]
      if (hover && hover !== hit?.object) hover.material.emissive?.setHex(0x000000)
      hover = hit?.object || null
      if (hover) { hover.material.emissive.setHex(0x333322); const b = hover.userData; tip.current.style.display = 'block'; tip.current.style.left = e.clientX - rc.left + 14 + 'px'; tip.current.style.top = e.clientY - rc.top + 14 + 'px'
        tip.current.innerHTML = `<b>${b.path}</b><br>${b.loc} lines · complexity ${b.complexity} · ${b.commits} commits` } else tip.current.style.display = 'none'
    }
    const click = () => hover && nav(`/r/${id}/files?path=${encodeURIComponent(hover.userData.path)}&line=1`)
    r.domElement.addEventListener('mousemove', move); r.domElement.addEventListener('click', click)
    let raf; const loop = () => { ctl.update(); r.render(scene, cam); raf = requestAnimationFrame(loop) }; loop()
    const resize = () => { r.setSize(W(), H()); cam.aspect = W() / H(); cam.updateProjectionMatrix() }; window.addEventListener('resize', resize)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize); r.dispose(); if (r.domElement.parentNode === el) el.removeChild(r.domElement) }
  }, [data, metric])
  return (
    <div className="city">
      <div ref={mount} style={{ position: 'absolute', inset: 0 }}><div ref={tip} className="tip" /></div>
      <div className="hud"><div className="pill"><b>Code city</b><select style={{ width: 'auto', border: 'none' }} value={metric} onChange={(e) => setMetric(e.target.value)}>{METRICS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <span className="muted">footprint = lines · height = complexity · click a building to open it</span></div>{loading && <div className="pill"><Spinner /></div>}</div>
    </div>
  )
}
