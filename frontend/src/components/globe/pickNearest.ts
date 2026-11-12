/**
 * pickNearest: which background satellite is under the pointer? Pure math, no raycasting, no allocation.
 *
 * Projects each point of the traffic layer's position array to screen space with the camera's own matrices and
 * returns the index of the nearest one within `maxPx`. A point is skipped when
 *   - it is a parked (0,0,0) point,
 *   - the straight line from the camera to it passes through the Earth sphere (hidden behind the globe), or
 *   - it is behind the camera.
 * About 400 points x ~40 flops: far below a frame budget (see the timing in the Task E1 report).
 */
export type PickCamera = {
  matrixWorldInverse: { elements: ArrayLike<number> };
  projectionMatrix: { elements: ArrayLike<number> };
};

/**
 * @param positions  x,y,z triples in scene units (the Points layer's array)
 * @param cam        camera whose matrices are current (call camera.updateMatrixWorld() first)
 * @param camPos     camera position in world space
 * @param radius     Earth radius in scene units (globe.getGlobeRadius(), 100)
 * @param w,h        canvas size in CSS px; px,py = pointer relative to the canvas
 * @returns index of the nearest visible point within maxPx, or -1
 */
export function pickNearest(
  positions: ArrayLike<number>, n: number, cam: PickCamera, camPos: { x: number; y: number; z: number },
  radius: number, w: number, h: number, px: number, py: number, maxPx: number,
): number {
  const v = cam.matrixWorldInverse.elements;
  const p = cam.projectionMatrix.elements;
  const { x: cx, y: cy, z: cz } = camPos;
  const r2 = radius * radius;
  let best = -1;
  let bestD = maxPx * maxPx;

  for (let i = 0; i < n; i += 1) {
    const x = positions[i * 3], y = positions[i * 3 + 1], z = positions[i * 3 + 2];
    if (x === 0 && y === 0 && z === 0) continue;

    // Hidden behind the Earth? closest approach of segment camera -> point to the origin
    const dx = x - cx, dy = y - cy, dz = z - cz;
    const dd = dx * dx + dy * dy + dz * dz;
    let t = dd > 0 ? -(cx * dx + cy * dy + cz * dz) / dd : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const qx = cx + t * dx, qy = cy + t * dy, qz = cz + t * dz;
    if (qx * qx + qy * qy + qz * qz < r2) continue;

    // Project (matrices are column-major, as in three.js)
    const vx = v[0] * x + v[4] * y + v[8] * z + v[12];
    const vy = v[1] * x + v[5] * y + v[9] * z + v[13];
    const vz = v[2] * x + v[6] * y + v[10] * z + v[14];
    const cw = p[3] * vx + p[7] * vy + p[11] * vz + p[15];
    if (cw <= 0) continue;
    const sx = ((p[0] * vx + p[4] * vy + p[8] * vz + p[12]) / cw * 0.5 + 0.5) * w;
    const sy = (0.5 - (p[1] * vx + p[5] * vy + p[9] * vz + p[13]) / cw * 0.5) * h;
    const d = (sx - px) * (sx - px) + (sy - py) * (sy - py);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}
