document.addEventListener('DOMContentLoaded', () => {

    // --- 1. Theme Toggle Logic ---
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        const sunIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>';
        const moonIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>';

        const setTheme = (theme) => {
            localStorage.setItem('theme', theme);
            if (theme === 'dark') {
                document.documentElement.classList.add('dark-mode');
                themeToggle.innerHTML = moonIcon;
            } else {
                document.documentElement.classList.remove('dark-mode');
                themeToggle.innerHTML = sunIcon;
            }
        };

        const currentTheme = localStorage.getItem('theme') || 'light';
        setTheme(currentTheme);

        themeToggle.addEventListener('click', () => {
            const newTheme = document.documentElement.classList.contains('dark-mode') ? 'light' : 'dark';
            setTheme(newTheme);
        });
    }

    // --- 2. Bulk Actions Dropdown Logic ---
    const bulkBtn = document.getElementById('bulk-actions-btn');
    const bulkContent = document.getElementById('bulk-actions-content');
    
    if (bulkBtn && bulkContent) {
        // Toggle open/close on button click
        bulkBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent window click from immediately closing it
            bulkContent.classList.toggle('show');
        });

        // Close if clicking anywhere outside
        window.addEventListener('click', () => {
            if (bulkContent.classList.contains('show')) {
                bulkContent.classList.remove('show');
            }
        });

        // Prevent closing if clicking inside the dropdown (e.g. on a form)
        bulkContent.addEventListener('click', (e) => {
            e.stopPropagation();
        });
    }

    // --- 3. Expandable Row Logic ---
    document.querySelectorAll('.domain-row').forEach(row => {
        row.addEventListener('click', (e) => {
            // Don't toggle row if clicking a link, button, or sort icon
            if (e.target.closest('a') || e.target.closest('button') || e.target.closest('.sort-icon')) {
                return;
            }
            
            const targetId = row.getAttribute('data-target');
            const targetRow = document.querySelector(targetId);
            
            if (targetRow) {
                if (targetRow.style.display === 'table-row') {
                    targetRow.style.display = 'none';
                    row.classList.remove('is-open');
                } else {
                    targetRow.style.display = 'table-row';
                    row.classList.add('is-open');
                }
            }
        });
    });

    // --- 4. Table Sorting Logic (Persistent) ---
    const tableBody = document.querySelector('.domain-table tbody');
    const resetBtn = document.getElementById('global-reset-btn');
    
    if (tableBody) {
        const sortIcons = document.querySelectorAll('.sort-icon');
        let currentSort = { key: 'default', direction: 'asc' };

        // Load saved sort from LocalStorage
        const savedSort = localStorage.getItem('domainTableSort');
        
        function getSortValue(row, key) {
            switch (key) {
                case 'domain-name':
                    return row.querySelector('td:nth-child(1) strong').innerText.toLowerCase();
                case 'ddns-status':
                    return row.querySelector('td:nth-child(2) span').innerText.toLowerCase();
                case 'ssl-expire':
                    const text = row.querySelector('td:nth-child(3)').innerText.trim();
                    if (text.includes('-')) return text; 
                    if (text === 'Missing') return '1000-01-01';
                    return '1000-01-02'; // N/A
                default:
                    return parseInt(row.dataset.defaultOrder, 10);
            }
        }

        function sortTable(sortKey, direction = null) {
            // Determine direction
            if (!direction) {
                if (currentSort.key === sortKey && currentSort.direction === 'asc') {
                    direction = 'desc';
                } else {
                    direction = 'asc';
                }
            }
            
            currentSort.key = sortKey;
            currentSort.direction = direction;

            // Save to storage
            localStorage.setItem('domainTableSort', JSON.stringify(currentSort));

            // Show/Hide Reset Button
            if (resetBtn) {
                resetBtn.style.display = 'inline-flex';
            }

            // Update icons
            sortIcons.forEach(icon => {
                icon.classList.remove('asc', 'desc');
                if (icon.dataset.sortKey === sortKey) {
                    icon.classList.add(direction);
                }
            });

            // Sort rows
            const rows = Array.from(tableBody.querySelectorAll('tr.domain-row'));
            const rowPairs = rows.map(row => ({
                main: row,
                details: row.nextElementSibling,
                sortValue: getSortValue(row, sortKey)
            }));

            rowPairs.sort((a, b) => {
                if (a.sortValue < b.sortValue) return direction === 'asc' ? -1 : 1;
                if (a.sortValue > b.sortValue) return direction === 'asc' ? 1 : -1;
                return 0;
            });

            // Re-append
            rowPairs.forEach(pair => {
                tableBody.appendChild(pair.main);
                if (pair.details) {
                    tableBody.appendChild(pair.details);
                }
            });
        }

        // Add listeners
        sortIcons.forEach(icon => {
            icon.addEventListener('click', () => {
                sortTable(icon.dataset.sortKey);
            });
        });

        // Apply saved sort on load
        if (savedSort) {
            try {
                const parsed = JSON.parse(savedSort);
                sortTable(parsed.key, parsed.direction);
            } catch(e) {
                console.error("Error parsing saved sort", e);
            }
        }
    }
});