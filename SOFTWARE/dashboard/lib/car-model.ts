/**
 * FW 2.3.4 — the digital twin's car, built from the SAME template outlines as the chassis the
 * team cut by hand (lib/car-model-data.json = the "F1 VCU Car Model" geometry: 58 sheet parts,
 * hinge and kingpin positions in mm). Every moving part hangs from its real pivot:
 *   front wheels  -> kingpin groups (steer about the kingpin), wheel spins inside them
 *   flaps         -> hinge groups at the front (FH) and rear (RH) hinge lines
 *   tie rods / pushrods are re-placed every frame between horn tips and arms
 * Car coordinates: x rearward from the nose tip, y + = LEFT, z up  ->  three (X = x, Y = z, Z = y).
 */
import * as THREE from 'three'
import DATA from './car-model-data.json'

export interface CarState { steer: number; fl: number; fr: number; rl: number; rr: number }
export interface CarRig { root: THREE.Group; apply: (s: CarState) => void; spin: (front: number, rear: number) => void; lengthMm: number }

type Poly = { outer: number[][]; holes: number[][][] }
const M = (DATA as any).meta
const COL = { ply: 0x8f99a6, sun: 0xecece8, servo: 0x2f5fa8, servoDark: 0x1f2329, flap: 0xff3b30, tyre: 0x141518, rim: 0xb9bec6, steel: 0x9aa1aa }

function mat(c: number, o: THREE.MeshStandardMaterialParameters = {}) { return new THREE.MeshStandardMaterial({ color: c, roughness: 0.6, metalness: 0.05, ...o }) }

function flipWinding(g: THREE.BufferGeometry) {
  for (const key of ['position', 'normal', 'uv']) {
    const a = g.attributes[key] as THREE.BufferAttribute | undefined
    if (!a) continue
    const n = a.itemSize, arr = a.array as Float32Array
    for (let i = 0; i < arr.length; i += 3 * n)
      for (let k = 0; k < n; k++) { const t = arr[i + n + k]; arr[i + n + k] = arr[i + 2 * n + k]; arr[i + 2 * n + k] = t }
    a.needsUpdate = true
  }
}
function extruded(poly: Poly, plane: string, d0: number, t: number) {
  const sh = new THREE.Shape(poly.outer.map((p) => new THREE.Vector2(p[0], p[1])))
  for (const h of poly.holes) sh.holes.push(new THREE.Path(h.map((p) => new THREE.Vector2(p[0], p[1]))))
  const g = new THREE.ExtrudeGeometry(sh, { depth: t, bevelEnabled: false, curveSegments: 6 })
  const m = new THREE.Matrix4()
  if (plane === 'x-z') m.set(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, d0, 0, 0, 0, 1)
  else if (plane === 'x-y') m.set(1, 0, 0, 0, 0, 0, 1, d0, 0, 1, 0, 0, 0, 0, 0, 1)
  else m.set(0, 0, 1, d0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1)
  g.applyMatrix4(m)
  if (m.determinant() < 0) flipWinding(g)
  g.computeVertexNormals()
  return g
}

export function buildCar(): CarRig {
  const outer = new THREE.Group()
  const root = new THREE.Group()
  root.position.set(-280, 0, 0)
  outer.add(root)
  const mats: Record<string, THREE.Material> = { ply: mat(COL.ply, { roughness: 0.8 }), sun: mat(COL.sun, { roughness: 0.65 }), flap: mat(COL.flap, { roughness: 0.5 }) }
  const pivot = (px: number, pz: number, py: number) => { const g = new THREE.Group(); g.position.set(px, pz, py); root.add(g); return g }
  const G: Record<string, THREE.Group> = {
    FL: pivot(M.FA, 0, M.YKP), FR: pivot(M.FA, 0, -M.YKP),
    FLAP_FL: pivot(M.FH[0], M.FH[1], 0), FLAP_FR: pivot(M.FH[0], M.FH[1], 0),
    FLAP_RL: pivot(M.RH[0], M.RH[1], 0), FLAP_RR: pivot(M.RH[0], M.RH[1], 0),
    COVER: pivot(0, 0, 0), HORN: pivot(M.MG995.sx, 0, 0),
  }
  const addTo = <T extends THREE.Object3D>(group: string | null, obj: T): T => {
    const g = group ? G[group] : root
    if (group) obj.position.sub(g.position)
    obj.traverse((o) => { o.castShadow = true; o.receiveShadow = true })
    g.add(obj)
    return obj
  }
  for (const p of (DATA as any).parts) {
    const isFlap = (p.group || '').startsWith('FLAP')
    for (const poly of p.polys) addTo(p.group, new THREE.Mesh(extruded(poly, p.plane, p.d0, p.t), isFlap ? mats.flap : mats[p.cat]))
  }
  for (const r of (DATA as any).ribbons) {
    const top: number[][] = r.line, bot: number[][] = r.line.slice().reverse().map((p: number[]) => [p[0], p[1] - r.t])
    addTo(r.group, new THREE.Mesh(extruded({ outer: top.concat(bot), holes: [] }, 'x-z', r.w[0], r.w[1] - r.w[0]), mats.sun))
  }
  const asMat = (c: number | THREE.Material, o?: THREE.MeshStandardMaterialParameters) => (typeof c === 'number' ? mat(c, o) : c)
  const box = (x0: number, x1: number, y0: number, y1: number, z0: number, z1: number, c: number | THREE.Material, group: string | null = null, o?: THREE.MeshStandardMaterialParameters) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(Math.abs(x1 - x0), Math.abs(z1 - z0), Math.abs(y1 - y0)), asMat(c, o))
    m.position.set((x0 + x1) / 2, (z0 + z1) / 2, (y0 + y1) / 2)
    return addTo(group, m)
  }
  const cylY = (x: number, z: number, y0: number, y1: number, r: number, c: number | THREE.Material, group: string | null = null, seg = 40) => {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, Math.abs(y1 - y0), seg), asMat(c))
    m.rotation.x = Math.PI / 2; m.position.set(x, z, (y0 + y1) / 2)
    return addTo(group, m)
  }
  const cylZ = (x: number, y: number, z0: number, z1: number, r: number, c: number | THREE.Material, group: string | null = null, seg = 24) => {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, Math.abs(z1 - z0), seg), asMat(c))
    m.position.set(x, (z0 + z1) / 2, y)
    return addTo(group, m)
  }
  const tyreM = mat(COL.tyre, { roughness: 0.9 }), rimM = mat(COL.rim, { metalness: 0.4, roughness: 0.4 }), steelM = mat(COL.steel, { metalness: 0.6 })

  // wheels: a spin group on the axle line, inside the kingpin group (front) or the body (rear)
  const spinners: { g: THREE.Group; front: boolean }[] = []
  const wheel = (x: number, yIn: number, s: number, group: string | null) => {
    const y0 = s > 0 ? yIn : -yIn - M.W, yc = y0 + M.W / 2, face = (s > 0 ? y0 + M.W : y0) - yc
    const sp = new THREE.Group()
    const part = (r: number, a: number, b: number, m: THREE.Material, seg: number) => {
      const c = new THREE.Mesh(new THREE.CylinderGeometry(r, r, Math.abs(b - a), seg), m)
      c.rotation.x = Math.PI / 2; c.position.z = (a + b) / 2; sp.add(c)
    }
    part(M.R, -M.W / 2, M.W / 2, tyreM, 48)
    part(M.R - 9, face - s * 1.5, face + s * 0.6, rimM, 40)
    part(7, face - s * 0.5, face + s * 1.2, mat(0x555b63), 20)
    for (let k = 0; k < 5; k++) {
      const a = (k * 2 * Math.PI) / 5
      const spoke = new THREE.Mesh(new THREE.BoxGeometry(4, M.R - 14, 1.6), rimM)
      spoke.position.set((Math.sin(a) * (M.R - 14)) / 2, (Math.cos(a) * (M.R - 14)) / 2, face + s * 1.3)
      spoke.rotation.z = -a; sp.add(spoke)
    }
    sp.position.set(x, M.R, yc)
    addTo(group, sp)
    spinners.push({ g: sp, front: group !== null })
  }
  for (const [s, g] of [[1, 'FL'], [-1, 'FR']] as [number, string][]) {
    wheel(M.FA, M.YWF, s, g)
    cylY(M.FA, M.R, s * 55, s * 92, 3, steelM, g)
    cylZ(M.ARM[0], s * M.ARM[1], 55, 68, 1.6, steelM, g)
    cylZ(M.FA, s * M.YKP, 9, 62, 1.5, steelM, g)
  }
  for (const s of [1, -1]) {
    wheel(M.RA, M.YWR, s, null)
    cylY(M.RA, M.R, s * M.YMF, s * (M.YMF + 30), 3, steelM)
    const zc = M.R + M.EOFF
    cylY(M.RA, zc, s * (M.YMF - M.GBL), s * M.YMF, M.GB / 2, mat(0x6f747c, { metalness: 0.5, roughness: 0.45 }))
    cylY(M.RA, zc, s * (M.YMF - M.MOTL), s * (M.YMF - M.GBL), M.CAN / 2, mat(0xa9aeb5, { metalness: 0.6, roughness: 0.35 }))
  }
  // servos
  const S = M.MG995
  box(S.x0, S.x0 + S.L, -S.W / 2, S.W / 2, S.zfloor, S.zfloor + S.H, 0x23324a)
  box(S.sx - 22, S.sx + 4, -3, 3, 65.5, 68, 0xf4f4f4, 'HORN')
  const R6 = M.MG996
  for (const s of [1, -1]) {
    const yIn = R6.ypl - R6.tab, yOut = R6.ypl + R6.H - R6.tab
    box(R6.x0, R6.x0 + R6.L, s * yIn, s * yOut, R6.cz - R6.W / 2, R6.cz + R6.W / 2, COL.servoDark)
  }
  const SG = M.SG
  for (const s of [1, -1]) box(SG.x0, SG.x0 + SG.L, s * (SG.ywall - SG.tab + 0.15), s * (SG.ywall + SG.H - SG.tab), SG.cz - SG.W / 2, SG.cz + SG.W / 2, COL.servo)
  // electronics
  box(330, 373, 32, 75, 25, 26.6, 0xb3261e); box(343, 366, 40, 60, 26.6, 51, 0x15171a, null, { metalness: 0.4 })
  box(215, 275, 32, 72, 21, 22.6, 0x1d4f9c)
  const bx0 = 340 - M.BOARD[0] / 2
  box(bx0, bx0 + M.BOARD[0], -M.BOARD[1] / 2, M.BOARD[1] / 2, 70, 71.6, 0x2f8f55)
  box(300, 351, -26, -5, 71.6, 75, 0x15171a)
  box(268, 272, -13.5, 13.5, 66, 106, 0x1b1d20)
  cylZ(230, -55, 60, 71, 6, 0x2a2d31)
  // flap horns live inside the flap groups
  for (const [g, s] of [['FLAP_FL', 1], ['FLAP_FR', -1]] as [string, number][]) box(M.FH[0] - 2, M.FH[0] + 2, s * 25, s * 27, M.FH[1], M.FH[1] + 10, 0xf4f4f4, g)
  for (const [g, s] of [['FLAP_RL', 1], ['FLAP_RR', -1]] as [string, number][]) box(M.RH[0] - 16, M.RH[0], s * 47, s * 49, M.RH[1] - 2, M.RH[1] + 1, 0xf4f4f4, g)
  // linkages
  const rodM = mat(0x3a3f46, { metalness: 0.6 })
  const rod = (r: number) => { const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 1, 10), rodM); root.add(m); return m }
  const UP = new THREE.Vector3(0, 1, 0)
  const place = (m: THREE.Mesh, a: number[], b: number[]) => {
    const A = new THREE.Vector3(a[0], a[2], a[1]), B = new THREE.Vector3(b[0], b[2], b[1])
    const d = B.clone().sub(A)
    m.position.copy(A).add(B).multiplyScalar(0.5); m.scale.set(1, d.length(), 1)
    m.quaternion.setFromUnitVectors(UP, d.normalize())
  }
  const tieL = rod(0.9), tieR = rod(0.9), pFL = rod(0.75), pFR = rod(0.75), pRL = rod(0.75), pRR = rod(0.75)
  const rot = (px: number, py: number, x: number, y: number, a: number) => {
    const dx = x - px, dy = y - py, c = Math.cos(a), s = Math.sin(a)
    return [px + dx * c + dy * s, py - dx * s + dy * c]
  }
  const rad = THREE.MathUtils.degToRad
  const apply = (st: CarState) => {
    const a = rad(st.steer)                                   // + = LEFT, the firmware's convention
    G.FL.rotation.y = a; G.FR.rotation.y = a; G.HORN.rotation.y = -a
    const tip = rot(M.MG995.sx, 0, M.MG995.sx - 20, 0, -a)
    const aL = rot(M.FA, M.YKP, M.ARM[0], M.ARM[1], a), aR = rot(M.FA, -M.YKP, M.ARM[0], -M.ARM[1], a)
    place(tieL, [tip[0], tip[1], 67], [aL[0], aL[1], 67]); place(tieR, [tip[0], tip[1], 67], [aR[0], aR[1], 67])
    G.FLAP_FL.rotation.z = rad(st.fl); G.FLAP_FR.rotation.z = rad(st.fr)
    G.FLAP_RL.rotation.z = rad(st.rl); G.FLAP_RR.rotation.z = rad(st.rr)
    for (const [m, t, s] of [[pFL, rad(st.fl), 1], [pFR, rad(st.fr), -1]] as [THREE.Mesh, number, number][])
      place(m, [M.SG.sx - 10 * Math.sin(t), s * 26, M.SG.cz + 10 * Math.cos(t)], [M.FH[0] - 10 * Math.sin(t), s * 26, M.FH[1] + 10 * Math.cos(t)])
    for (const [m, t, s] of [[pRL, rad(st.rl), 1], [pRR, rad(st.rr), -1]] as [THREE.Mesh, number, number][])
      place(m, [M.MG996.sx + 15 * Math.cos(t), s * 48, M.MG996.cz - 15 * Math.sin(t)], [M.RH[0] - 15 * Math.cos(t), s * 48, M.RH[1] - 15 * Math.sin(t)])
  }
  const spin = (front: number, rear: number) => { for (const w of spinners) w.g.rotation.z += w.front ? front : rear }
  outer.traverse((o) => { if ((o as THREE.Mesh).isMesh) { o.castShadow = true; o.receiveShadow = true } })
  apply({ steer: 0, fl: 0, fr: 0, rl: 0, rr: 0 })
  return { root: outer, apply, spin, lengthMm: 560 }
}
