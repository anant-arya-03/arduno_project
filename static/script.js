(function () {
    var errorValue = document.getElementById("errorValue");

    if (typeof THREE === "undefined") {
        if (errorValue) {
            errorValue.textContent = "3D engine failed to load. Please refresh and check network access.";
        }
        return;
    }

    var container = document.getElementById("scene-container");
    if (!container) {
        if (errorValue) {
            errorValue.textContent = "Scene container missing.";
        }
        return;
    }

    var angleValue = document.getElementById("angleValue");
    var distanceValue = document.getElementById("distanceValue");
    var modeValue = document.getElementById("modeValue");
    var countValue = document.getElementById("countValue");
    var saveButton = document.getElementById("saveButton");

    var config = {
        dataEndpoint: "/data",
        saveEndpoint: "/save",
        pollIntervalMs: 130,
        pointLifetimeMs: 3200,
        wavePeriodMs: 2100,
        maxVisiblePoints: 220,
        angleMergeDeg: 2,
        maxDistance: Number(window.RADAR_CONFIG && window.RADAR_CONFIG.maxDistance) || 250
    };

    if (saveButton) {
        saveButton.addEventListener("click", function () {
            window.location.href = config.saveEndpoint;
        });
    }

    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0x030a08);
    scene.fog = new THREE.Fog(0x020806, 120, 430);

    var camera = new THREE.PerspectiveCamera(58, 1, 0.1, 1200);
    camera.position.set(0, 146, 205);

    var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(Math.max(container.clientWidth, 100), Math.max(container.clientHeight, 100));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    if (renderer.outputColorSpace !== undefined) {
        renderer.outputColorSpace = THREE.SRGBColorSpace;
    }
    container.appendChild(renderer.domElement);

    if (typeof THREE.OrbitControls !== "function") {
        if (errorValue) {
            errorValue.textContent = "Orbit controls failed to load from CDN.";
        }
        return;
    }

    var controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.enablePan = false;
    controls.maxPolarAngle = Math.PI * 0.495;
    controls.minDistance = 80;
    controls.maxDistance = 420;

    var ambient = new THREE.AmbientLight(0x9afec8, 0.35);
    scene.add(ambient);

    var pointLight = new THREE.PointLight(0x66ffc1, 1.2, 420, 2);
    pointLight.position.set(0, 95, 0);
    pointLight.castShadow = true;
    scene.add(pointLight);

    var fillLight = new THREE.PointLight(0xfff2b0, 0.35, 460, 2);
    fillLight.position.set(-120, 80, 90);
    scene.add(fillLight);

    var root = new THREE.Group();
    scene.add(root);

    var gridMaterial = new THREE.MeshStandardMaterial({
        color: 0x0d2a22,
        emissive: 0x0f4e3d,
        emissiveIntensity: 0.2,
        roughness: 0.55,
        metalness: 0.15,
        transparent: true,
        opacity: 0.92
    });
    var baseDisk = new THREE.Mesh(new THREE.CircleGeometry(config.maxDistance, 140), gridMaterial);
    baseDisk.rotation.x = -Math.PI / 2;
    baseDisk.receiveShadow = true;
    root.add(baseDisk);

    var ringGroup = new THREE.Group();
    root.add(ringGroup);

    for (var radius = 20; radius <= config.maxDistance; radius += 20) {
        var ringOpacity = 0.16 - radius / (config.maxDistance * 9.5);
        var ringGeometry = new THREE.RingGeometry(radius - 0.28, radius + 0.28, 120);
        var ringMaterial = new THREE.MeshBasicMaterial({
            color: 0x69ffaf,
            transparent: true,
            opacity: Math.max(ringOpacity, 0.04),
            side: THREE.DoubleSide
        });
        var ring = new THREE.Mesh(ringGeometry, ringMaterial);
        ring.rotation.x = -Math.PI / 2;
        ringGroup.add(ring);
    }

    for (var deg = 0; deg < 360; deg += 15) {
        var rad = THREE.MathUtils.degToRad(deg);
        var lineGeometry = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(0, 0.04, 0),
            new THREE.Vector3(config.maxDistance * Math.cos(rad), 0.04, config.maxDistance * Math.sin(rad))
        ]);
        var line = new THREE.Line(
            lineGeometry,
            new THREE.LineBasicMaterial({ color: 0x4be397, transparent: true, opacity: 0.18 })
        );
        root.add(line);
    }

    var sweepPivot = new THREE.Group();
    root.add(sweepPivot);

    var sweepLineGeometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 1.2, 0),
        new THREE.Vector3(config.maxDistance, 1.2, 0)
    ]);
    var sweepLineMaterial = new THREE.LineBasicMaterial({
        color: 0x72ffbf,
        transparent: true,
        opacity: 0.75
    });
    var sweepLine = new THREE.Line(sweepLineGeometry, sweepLineMaterial);
    sweepPivot.add(sweepLine);

    var sweepFanShape = new THREE.Shape();
    sweepFanShape.moveTo(0, 0);
    sweepFanShape.absarc(0, 0, config.maxDistance, -0.09, 0.09, false);
    sweepFanShape.lineTo(0, 0);
    var sweepFan = new THREE.Mesh(
        new THREE.ShapeGeometry(sweepFanShape),
        new THREE.MeshBasicMaterial({
            color: 0x60fdb2,
            transparent: true,
            opacity: 0.13,
            side: THREE.DoubleSide,
            depthWrite: false
        })
    );
    sweepFan.rotation.x = -Math.PI / 2;
    sweepFan.position.y = 0.4;
    sweepPivot.add(sweepFan);

    var waveGroup = new THREE.Group();
    root.add(waveGroup);

    var waves = [];
    function createWave(offsetMs) {
        var wave = new THREE.Mesh(
            new THREE.RingGeometry(1, 1.6, 64),
            new THREE.MeshBasicMaterial({
                color: 0x7cffc6,
                transparent: true,
                opacity: 0.2,
                side: THREE.DoubleSide,
                depthWrite: false
            })
        );
        wave.rotation.x = -Math.PI / 2;
        wave.position.y = 0.26;
        waveGroup.add(wave);
        waves.push({ mesh: wave, material: wave.material, offsetMs: offsetMs });
    }

    createWave(0);
    createWave(config.wavePeriodMs / 3);
    createWave((config.wavePeriodMs * 2) / 3);

    var pointGeometry = new THREE.SphereGeometry(1.5, 14, 14);
    var haloGeometry = new THREE.SphereGeometry(2.8, 10, 10);
    var pointGroup = new THREE.Group();
    root.add(pointGroup);

    var pointMap = new Map();

    function heatColor(distance) {
        var normalized = distance / config.maxDistance;
        if (normalized < 0.33) {
            return new THREE.Color(0xff574a);
        }
        if (normalized < 0.66) {
            return new THREE.Color(0xffd84c);
        }
        return new THREE.Color(0x52ff9f);
    }

    function getPointId(point) {
        var ts = Number(point.ts || 0).toFixed(5);
        var angle = Number(point.angle || 0).toFixed(2);
        var distance = Number(point.distance || 0).toFixed(2);
        return ts + ":" + angle + ":" + distance;
    }

    function mergePointsByAngle(points) {
        var byBin = new Map();
        var binSize = Math.max(0.5, Number(config.angleMergeDeg) || 2);

        for (var i = 0; i < points.length; i += 1) {
            var point = points[i];
            var angle = Number(point.angle) || 0;
            var key = String(Math.round(angle / binSize));
            var prev = byBin.get(key);

            if (!prev || (Number(point.ts) || 0) >= (Number(prev.ts) || 0)) {
                byBin.set(key, point);
            }
        }

        var merged = Array.from(byBin.values());
        merged.sort(function (a, b) {
            return (Number(a.ts) || 0) - (Number(b.ts) || 0);
        });

        if (merged.length > config.maxVisiblePoints) {
            merged = merged.slice(merged.length - config.maxVisiblePoints);
        }

        return merged;
    }

    function createPointEntry(point) {
        var color = heatColor(Number(point.distance) || 0);

        var mat = new THREE.MeshStandardMaterial({
            color: color,
            emissive: color,
            emissiveIntensity: 0.95,
            roughness: 0.28,
            metalness: 0.6,
            transparent: true,
            opacity: 1
        });

        var sphere = new THREE.Mesh(pointGeometry, mat);
        sphere.castShadow = true;

        var haloMat = new THREE.MeshBasicMaterial({
            color: color,
            transparent: true,
            opacity: 0.22,
            depthWrite: false
        });
        var halo = new THREE.Mesh(haloGeometry, haloMat);
        halo.scale.setScalar(1.25);
        sphere.add(halo);

        pointGroup.add(sphere);

        return {
            mesh: sphere,
            material: mat,
            haloMaterial: haloMat,
            createdMs: Number(point.ts || Date.now() / 1000) * 1000,
            pulseOffset: Math.random() * Math.PI * 2
        };
    }

    function updatePointVisual(entry, point) {
        var angleDeg = Number(point.angle) || 0;
        var distance = Math.max(0, Number(point.distance) || 0);
        var intensity = Math.min(1, Math.max(0, Number(point.intensity) || 0));
        var frequency = Number(point.frequency);

        var angleRad = THREE.MathUtils.degToRad(angleDeg);
        var x = distance * Math.cos(angleRad);
        var z = distance * Math.sin(angleRad);

        var yFromIntensity = 2 + intensity * 18;
        var yFromFrequency = Number.isFinite(frequency) ? Math.min(6, frequency * 0.08) : 0;

        entry.mesh.position.set(x, yFromIntensity + yFromFrequency, z);

        var color = heatColor(distance);
        entry.material.color.copy(color);
        entry.material.emissive.copy(color);
        entry.haloMaterial.color.copy(color);
    }

    function removePoint(id) {
        var entry = pointMap.get(id);
        if (!entry) {
            return;
        }
        pointGroup.remove(entry.mesh);
        entry.material.dispose();
        entry.haloMaterial.dispose();
        pointMap.delete(id);
    }

    function syncPoints(points) {
        if (points.length > config.maxVisiblePoints * 6) {
            points = points.slice(points.length - config.maxVisiblePoints * 6);
        }

        points = mergePointsByAngle(points);

        var seen = new Set();

        for (var i = 0; i < points.length; i += 1) {
            var point = points[i];
            var id = getPointId(point);
            seen.add(id);

            var entry = pointMap.get(id);
            if (!entry) {
                entry = createPointEntry(point);
                pointMap.set(id, entry);
            }
            updatePointVisual(entry, point);
        }

        var stale = [];
        pointMap.forEach(function (_entry, id) {
            if (!seen.has(id)) {
                stale.push(id);
            }
        });
        for (var j = 0; j < stale.length; j += 1) {
            removePoint(stale[j]);
        }
    }

    function setUiText(node, text) {
        if (node) {
            node.textContent = text;
        }
    }

    function updateHud(current, mode, count, lastError) {
        var angle = Number(current && current.angle) || 0;
        var distance = Number(current && current.distance) || 0;

        setUiText(angleValue, angle.toFixed(1) + " deg");
        setUiText(distanceValue, distance.toFixed(1) + " cm");
        setUiText(modeValue, "mode: " + String(mode || "--"));
        setUiText(countValue, "points: " + String(count || 0));

        if (lastError) {
            setUiText(errorValue, "Reader notice: " + lastError);
        } else {
            setUiText(errorValue, "");
        }

        sweepPivot.rotation.y = THREE.MathUtils.degToRad(angle);
    }

    var isFetching = false;

    async function fetchData() {
        if (isFetching) {
            return;
        }
        isFetching = true;

        try {
            var response = await fetch(config.dataEndpoint, { cache: "no-store" });
            if (!response.ok) {
                throw new Error("Radar API unavailable");
            }

            var payload = await response.json();
            syncPoints(Array.isArray(payload.points) ? payload.points : []);
            updateHud(payload.current || {}, payload.mode, payload.count, payload.lastError);
        } catch (_error) {
            setUiText(modeValue, "mode: reconnecting");
            setUiText(errorValue, "Waiting for backend stream...");
        } finally {
            isFetching = false;
        }
    }

    function animate(nowMs) {
        requestAnimationFrame(animate);
        controls.update();

        pointLight.intensity = 1.1 + 0.2 * Math.sin(nowMs / 500);
        sweepLineMaterial.opacity = 0.62 + 0.18 * Math.sin(nowMs / 170);
        sweepFan.material.opacity = 0.08 + 0.08 * (0.5 + 0.5 * Math.sin(nowMs / 200));

        for (var i = 0; i < waves.length; i += 1) {
            var wave = waves[i];
            var cycle = ((nowMs + wave.offsetMs) % config.wavePeriodMs) / config.wavePeriodMs;
            var radius = 5 + cycle * config.maxDistance;
            wave.mesh.scale.set(radius, radius, 1);
            wave.material.opacity = 0.34 * (1 - cycle);
        }

        var now = Date.now();
        var expired = [];

        pointMap.forEach(function (entry, id) {
            var age = now - entry.createdMs;
            var life = 1 - age / config.pointLifetimeMs;

            if (life <= 0) {
                expired.push(id);
                return;
            }

            entry.material.opacity = Math.max(0.1, life);
            entry.material.emissiveIntensity = 0.35 + 1.2 * life;
            entry.haloMaterial.opacity = Math.max(0.03, life * 0.24);

            var pulse = 0.96 + 0.08 * Math.sin(nowMs / 250 + entry.pulseOffset);
            entry.mesh.scale.setScalar(pulse);
        });

        for (var j = 0; j < expired.length; j += 1) {
            removePoint(expired[j]);
        }

        renderer.render(scene, camera);
    }

    function onResize() {
        var width = Math.max(container.clientWidth, 100);
        var height = Math.max(container.clientHeight, 100);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height);
    }

    window.addEventListener("resize", onResize);
    onResize();

    fetchData();
    setInterval(fetchData, config.pollIntervalMs);
    requestAnimationFrame(animate);
})();
