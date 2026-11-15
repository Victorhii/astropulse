const canvas = document.getElementById('star-canvas');
const ctx = canvas.getContext('2d');

canvas.width = window.innerWidth;
canvas.height = window.innerHeight;

let particlesArray = [];

let mouse = {
    x: null,
    y: null,
    radius: 140 
};

window.addEventListener('mousemove', function(event) {
    mouse.x = event.x;
    mouse.y = event.y;
});

window.addEventListener('mouseout', function() {
    mouse.x = null;
    mouse.y = null;
});

window.addEventListener('resize', function() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    init();
});

class Particle {
    constructor(x, y) {
        this.x = x;
        this.y = y;
        this.baseX = x; 
        this.baseY = y; 
        
        // 1. SLIGHTLY BIGGER & BRIGHTER
        // Size range: 0.8px to 2.6px (previously 0.5px to 2.0px)
        this.size = Math.random() * 1.8 + 0.8; 
        
        // Brightness / Opacity: 0.45 to 1.0 (previously 0.2 to 1.0)
        let alpha = Math.random() * 0.55 + 0.45;
        this.color = `rgba(255, 255, 255, ${alpha})`;
        
        // 2. REDUCED IDLE MOVEMENT
        this.angle = Math.random() * Math.PI * 2;
        this.idleSpeed = Math.random() * 0.008 + 0.003; // Decreased floating rotation speed
        this.orbitRadius = Math.random() * 12 + 3;      // Decreased float distance (was 25)
        
        // Easing speed (controls how fast stars push/return without bouncing)
        this.ease = Math.random() * 0.03 + 0.025;
    }
    
    draw() {
        ctx.fillStyle = this.color;
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.closePath();
        ctx.fill();
    }
    
    update() {
        // 1. Calculate gentle idle target position
        this.angle += this.idleSpeed;
        let targetX = this.baseX + Math.cos(this.angle) * this.orbitRadius;
        let targetY = this.baseY + Math.sin(this.angle) * this.orbitRadius;
        
        // 2. Mouse Repulsion target calculation
        if (mouse.x != null && mouse.y != null) {
            let dx = mouse.x - this.x;
            let dy = mouse.y - this.y;
            let distance = Math.sqrt(dx * dx + dy * dy);
            
            if (distance < mouse.radius) {
                let force = (mouse.radius - distance) / mouse.radius;
                let forceDirectionX = dx / distance;
                let forceDirectionY = dy / distance;
                
                let pushStrength = 90;
                targetX -= forceDirectionX * force * pushStrength;
                targetY -= forceDirectionY * force * pushStrength;
            }
        }
        
        // 3. REMOVED ELASTIC/BOUNCE EFFECT
        // Uses smooth linear easing instead of velocity vectors so stars slide directly to their target
        this.x += (targetX - this.x) * this.ease;
        this.y += (targetY - this.y) * this.ease;
        
        // Reduced upward background drift speed (was 0.15)
        this.baseY -= 0.06;
        if (this.baseY < -50) {
            this.baseY = canvas.height + 50;
            this.y = canvas.height + 50;
        }
        
        this.draw();
    }
}

function init() {
    particlesArray = [];
    let numberOfParticles = (canvas.width * canvas.height) / 1600; 
    
    for (let i = 0; i < numberOfParticles; i++) {
        let x = Math.random() * canvas.width;
        let y = Math.random() * canvas.height;
        particlesArray.push(new Particle(x, y));
    }
}

function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < particlesArray.length; i++) {
        particlesArray[i].update();
    }
    requestAnimationFrame(animate);
}

init();
animate();