document.addEventListener('DOMContentLoaded', () => {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('overlay');
    const openBtn = document.getElementById('openBtn');
    const closeBtn = document.getElementById('closeBtn');

    function expandMenu() {
        sidebar.classList.add('expanded');
        overlay.classList.add('active');
    }
    function retractMenu() {
        sidebar.classList.remove('expanded');
        overlay.classList.remove('active');
    }

    openBtn.addEventListener('click', expandMenu);
    closeBtn.addEventListener('click', retractMenu);
    overlay.addEventListener('click', retractMenu);
});