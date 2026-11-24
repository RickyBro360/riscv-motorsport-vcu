'use client'

import { useMemo, useRef } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { ContactShadows, Grid, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { useTelemetry } from '@/contexts/telemetry-context'
import type { CameraPreset } from '@/types/telemetry'
import { buildCar } from '@/lib/car-model'

const DEG = Math.PI / 180
const presetPositions: Record<CameraPreset, [number, number, number]> = { top: [0, 6, 0.1], front: [0, 1.8, 6], rear: [0, 1.7, -6], driver: [3.2, 2.2, 3.6] }

const SCALE = 0.0062      // model is in mm (560 mm long) -> ~3.5 scene units
const GROUND_Y = -0.28

/** FW 2.3.4: the car is built once from the chassis template geometry (lib/car-model.ts); wheels and
 * flaps hang from their real kingpins / hinge lines and are driven by the telemetry every frame. */
function Car({ resetKey = 0 }: { resetKey?: number }) {
  const group = useRef<THREE.Group>(null)
  const { frame } = useTelemetry()
  const rig = useMemo(() => buildCar(), [])
  const target = useRef({ roll: 0, pitch: 0, yaw: 0, steer: 0, fl: 0, fr: 0, rl: 0, rr: 0, vFront: 0, vRear: 0 })
  // FW 2.3.11: yaw. The VCU's yaw is gyro-only (it drifts slowly), so the twin shows it RELATIVE to the
  // heading when the page opened or Reset was pressed.
  const yawRef = useRef<number | null>(null)
  const yawShown = useRef(0)
  const lastReset = useRef(resetKey)
  const cur = useRef({ steer: 0, fl: 0, fr: 0, rl: 0, rr: 0 })
  if (frame) target.current = {
    roll: frame.imu.roll, pitch: frame.imu.pitch, yaw: frame.imu.yaw ?? 0, steer: frame.steering,
    fl: frame.wing.frontLeft, fr: frame.wing.frontRight, rl: frame.wing.rearLeft, rr: frame.wing.rearRight,
    vFront: ((frame.wheels?.fl ?? 0) + (frame.wheels?.fr ?? 0)) / 2,
    vRear: frame.wheels ? (frame.wheels.rl + frame.wheels.rr) / 2 : frame.speed,
  }
  useFrame((_, dt) => {
    const t = target.current, c = cur.current, k = Math.min(1, dt * 12)
    for (const key of ['steer', 'fl', 'fr', 'rl', 'rr'] as const) c[key] += (t[key] - c[key]) * k
    rig.apply(c)
    // wheel spin: real angular speed is too fast to read at 60 fps, so it is shown at 1/8 speed
    const w = (kmh: number) => ((kmh / 3.6) / 0.045) * dt * 0.125
    rig.spin(w(t.vFront), w(t.vRear))
    if (group.current) {
      // scene: car nose toward +Z, car LEFT = +X. VCU frame (FW 2.3.8, after `imu level` + `imu nose`): x forward,
      // y left, z up -> nose up = NEGATIVE pitch, left side up = POSITIVE roll.
      group.current.rotation.order = 'YXZ'              // heading, then pitch, then roll
      group.current.rotation.x = THREE.MathUtils.lerp(group.current.rotation.x, t.pitch * DEG, 0.12)
      group.current.rotation.z = THREE.MathUtils.lerp(group.current.rotation.z, t.roll * DEG, 0.12)
      if (lastReset.current !== resetKey) { lastReset.current = resetKey; yawRef.current = null }
      if (yawRef.current === null) yawRef.current = t.yaw
      let rel = t.yaw - yawRef.current                                  // VCU yaw: + = turned left (counter-clockwise from above)
      rel = ((rel + 540) % 360) - 180
      let d = rel - yawShown.current
      d = ((d + 540) % 360) - 180                                        // shortest way round
      yawShown.current += d * 0.12
      group.current.rotation.y = yawShown.current * DEG                  // three: + about Y turns the nose (+Z) to the left (+X)
    }
  })
  return <group ref={group} position={[0, GROUND_Y, 0]}><group rotation={[0, Math.PI / 2, 0]} scale={SCALE}><primitive object={rig.root} /></group></group>
}
function CameraRig({ preset, resetKey }: { preset: CameraPreset; resetKey: number }) {
  const { camera } = useThree()
  const controls = useRef<any>(null)
  useFrame(() => { if (resetKey > 0 && controls.current) { camera.position.lerp(new THREE.Vector3(...presetPositions[preset]), 0.08); controls.current.target.lerp(new THREE.Vector3(0, 0.05, 0), 0.08); controls.current.update() } })
  return <OrbitControls ref={controls} enablePan={false} minDistance={3} maxDistance={10} maxPolarAngle={Math.PI / 2.05} />
}
export function CarScene({ preset = 'driver', resetKey = 0 }: { preset?: CameraPreset; resetKey?: number }) {
  return <Canvas shadows dpr={[1, 2]} camera={{ position: presetPositions[preset], fov: 42 }} gl={{ antialias: true }}><color attach="background" args={['#09090b']} /><ambientLight intensity={0.75} /><directionalLight position={[4, 6, 3]} intensity={1.4} castShadow shadow-mapSize={[1024, 1024]} /><directionalLight position={[-4, 3, -2]} intensity={0.5} color="#3b82f6" /><spotLight position={[0, 5, 0]} intensity={0.6} angle={0.5} penumbra={1} color="#ff3b30" /><Car resetKey={resetKey} /><ContactShadows position={[0, -0.28, 0]} opacity={0.55} scale={8} blur={2.4} far={4} /><Grid position={[0, -0.28, 0]} args={[20, 20]} cellSize={0.5} cellThickness={0.6} cellColor="#1f1f22" sectionSize={2.5} sectionThickness={1} sectionColor="#2a2a2a" fadeDistance={18} fadeStrength={1} infiniteGrid /><CameraRig preset={preset} resetKey={resetKey} /></Canvas>
}
