import * as THREE from 'three';

// 1. Fetch Time Offset From Flask (/api/time -> timeapi.io)
async function getTimeApiOffset() {
  try {
    const requestSentAt = Date.now();
    const response = await fetch('/api/time');
    const data = await response.json();
    const networkDelay = (Date.now() - requestSentAt) / 2;

    console.log(`Time synced from ${data.source} (${data.timezone || 'UTC'})`);
    return data.timestamp_ms + networkDelay - Date.now();
  } catch (err) {
    console.warn('Could not reach /api/time, falling back to PC clock:', err);
    return 0;
  }
}

// 2. Live 24-Hour UTC Clock (#timenow) Synced to timeapi.io
function startSyncedClock(timeOffsetMs) {
  const timeElement = document.getElementById('timenow');
  if (!timeElement) {
    console.warn('Element #timenow not found in index.html');
    return;
  }

  function tick() {
    const syncedNow = new Date(Date.now() + timeOffsetMs);

    const dateText = syncedNow.toLocaleDateString('en-GB', {
      timeZone: 'UTC',
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    });

    const timeText = syncedNow.toLocaleTimeString('en-GB', {
      timeZone: 'UTC',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
      hourCycle: 'h23',
    });

    timeElement.textContent = `${dateText} | ${timeText} UTC`;
    setTimeout(tick, 1000 - syncedNow.getMilliseconds());
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) tick();
  });

  tick();
}

// 3. Lunar Day Counter (#lunar-day-text)
function updateLunarDayFromTime(syncedDate) {
  const lunarDayElement = document.getElementById('lunar-day-text');
  if (!lunarDayElement) {
    console.warn('Element #lunar-day-text not found in index.html');
    return;
  }

  const SYNODIC_MONTH = 29.530588853;
  const KNOWN_NEW_MOON = Date.UTC(2000, 0, 6, 18, 14, 0);
  const MS_PER_DAY = 86400000;

  const daysElapsed = (syncedDate.getTime() - KNOWN_NEW_MOON) / MS_PER_DAY;
  const lunarDay = ((daysElapsed % SYNODIC_MONTH) + SYNODIC_MONTH) % SYNODIC_MONTH;

  lunarDayElement.textContent = `${lunarDay.toFixed(1)} day of 29.5 lunar day`;
}

// 4. Three.js Scene, Camera & Renderer
const scene = new THREE.Scene();


const camera = new THREE.PerspectiveCamera(
  60,
  window.innerWidth / window.innerHeight,
  0.1,
  1000
);
camera.position.z = 3;
camera.position.x = -0.15;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);

function render() {
  renderer.render(scene, camera);
}

// 5. Load Moon Texture
const textureLoader = new THREE.TextureLoader();
const ballTexture = textureLoader.load(
  '/static/images/lunar_surface_8k.jpg',
  () => render()
);
ballTexture.colorSpace = THREE.SRGBColorSpace;

// 6. Create 3D Moon Mesh
const geometry = new THREE.SphereGeometry(1, 64, 64);
const material = new THREE.MeshStandardMaterial({
  map: ballTexture,
  roughness: 0.9,
  metalness: 0.1,
});
const ball = new THREE.Mesh(geometry, material);
scene.add(ball);

// 7. Lighting
const ambientLight = new THREE.AmbientLight(0xffffff, 0.05);
scene.add(ambientLight);

const directionalLight = new THREE.DirectionalLight(0xffffff, 2.2);
scene.add(directionalLight);

// 8. Calculate Sun Position & Earth-Facing Moon Orientation From Synced UTC Date
function updateMoonFromDate(moonMesh, sunLight, lightDistance = 7, date = new Date()) {
  const DEG2RAD = Math.PI / 180;
  const RAD2DEG = 180 / Math.PI;
  const normalizeDeg = (deg) => ((deg % 360) + 360) % 360;
  const wrapSignedDeg = (deg) => ((((deg + 180) % 360) + 360) % 360) - 180;

  const d = (date.getTime() - Date.UTC(2000, 0, 1, 12, 0, 0)) / 86400000;

  // Earth's Orbit Around the Sun
  const sunMeanAnomaly = normalizeDeg(357.5291 + 0.98560028 * d) * DEG2RAD;
  const sunMeanLon = normalizeDeg(280.4665 + 0.98564736 * d);
  const sunEquationOfCenter =
    1.9148 * Math.sin(sunMeanAnomaly) +
    0.0200 * Math.sin(2 * sunMeanAnomaly) +
    0.0003 * Math.sin(3 * sunMeanAnomaly);
  const sunEclipticLon = normalizeDeg(sunMeanLon + sunEquationOfCenter) * DEG2RAD;

  // Moon's Elliptical & Inclined Orbit Around Earth
  const moonMeanLon = normalizeDeg(218.3165 + 13.17639648 * d);
  const moonMeanAnomaly = normalizeDeg(134.9634 + 13.06499295 * d) * DEG2RAD;
  const moonArgLatitude = normalizeDeg(93.2721 + 13.22935024 * d) * DEG2RAD;
  const moonAscNode = normalizeDeg(125.0445 - 0.05295377 * d);
  const meanElongation = normalizeDeg(moonMeanLon - sunMeanLon) * DEG2RAD;

  const moonEclipticLonDeg = normalizeDeg(
    moonMeanLon +
      6.2886 * Math.sin(moonMeanAnomaly) +
      1.2740 * Math.sin(2 * meanElongation - moonMeanAnomaly) +
      0.6583 * Math.sin(2 * meanElongation)
  );
  const moonEclipticLon = moonEclipticLonDeg * DEG2RAD;

  const moonEclipticLat =
    5.128 * Math.sin(moonArgLatitude + 6.2886 * DEG2RAD * Math.sin(moonMeanAnomaly)) * DEG2RAD;

  // Tidal Locking (Near Side Facing Earth) + Real-Time Optical Libration
  const lunarEquatorInclination = 1.54242 * DEG2RAD;
  const nodeAngleW = normalizeDeg(moonEclipticLonDeg - moonAscNode) * DEG2RAD;

  const angleA = Math.atan2(
    Math.sin(nodeAngleW) * Math.cos(moonEclipticLat) * Math.cos(lunarEquatorInclination) -
      Math.sin(moonEclipticLat) * Math.sin(lunarEquatorInclination),
    Math.cos(nodeAngleW) * Math.cos(moonEclipticLat)
  );

  const librationLonDeg = wrapSignedDeg(angleA * RAD2DEG - moonArgLatitude * RAD2DEG);
  const librationLatRad = -Math.asin(
    Math.sin(nodeAngleW) * Math.cos(moonEclipticLat) * Math.sin(lunarEquatorInclination) +
      Math.sin(moonEclipticLat) * Math.cos(lunarEquatorInclination)
  );
  const librationLonRad = librationLonDeg * DEG2RAD;

  moonMesh.rotation.order = 'YXZ';
  moonMesh.rotation.y = -Math.PI / 2 - librationLonRad;
  moonMesh.rotation.x = librationLatRad;

  // Equatorial Coordinates & Sun Light Position
  const obliquity = (23.4393 - 0.0000004 * d) * DEG2RAD;

  const sunRA = Math.atan2(
    Math.cos(obliquity) * Math.sin(sunEclipticLon),
    Math.cos(sunEclipticLon)
  );
  const sunDec = Math.asin(Math.sin(obliquity) * Math.sin(sunEclipticLon));

  const moonRA = Math.atan2(
    Math.cos(obliquity) * Math.sin(moonEclipticLon) * Math.cos(moonEclipticLat) -
      Math.sin(obliquity) * Math.sin(moonEclipticLat),
    Math.cos(moonEclipticLon) * Math.cos(moonEclipticLat)
  );
  const moonDec = Math.asin(
    Math.sin(obliquity) * Math.sin(moonEclipticLon) * Math.cos(moonEclipticLat) +
      Math.cos(obliquity) * Math.sin(moonEclipticLat)
  );

  const cosElongation =
    Math.sin(sunDec) * Math.sin(moonDec) +
    Math.cos(sunDec) * Math.cos(moonDec) * Math.cos(sunRA - moonRA);
  const phaseAngle = Math.acos(Math.max(-1, Math.min(1, cosElongation)));

  const brightLimbAngle = Math.atan2(
    Math.cos(sunDec) * Math.sin(sunRA - moonRA),
    Math.sin(sunDec) * Math.cos(moonDec) -
      Math.cos(sunDec) * Math.sin(moonDec) * Math.cos(sunRA - moonRA)
  );

  const sunX = -lightDistance * Math.sin(phaseAngle) * Math.sin(brightLimbAngle);
  const sunY = lightDistance * Math.sin(phaseAngle) * Math.cos(brightLimbAngle);
  const sunZ = -lightDistance * Math.cos(phaseAngle);

  sunLight.position.set(sunX, sunY, sunZ);
}

// 9. Initialize Everything With Synced UTC Time
async function init() {
  const offsetMs = await getTimeApiOffset();
  const syncedDate = new Date(Date.now() + offsetMs);

  startSyncedClock(offsetMs);
  updateLunarDayFromTime(syncedDate);
  updateMoonFromDate(ball, directionalLight, 7, syncedDate);
  render();
}

init();

// 10. Handle Browser Resizing
window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  render();
});