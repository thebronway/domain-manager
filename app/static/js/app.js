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

    // --- 2. Expandable Row Logic ---
    document.querySelectorAll('.domain-row').forEach(row => {
        row.addEventListener('click', (e) => {
            // Don't toggle row if clicking a link or button
            if (e.target.tagName === 'A' || e.target.tagName === 'BUTTON' || e.target.closest('.btn') || e.target.closest('.sort-icon')) {
                return;
            }
            
            const targetId = row.getAttribute('data-target');
            const targetRow = document.querySelector(targetId);
            
            if (targetRow) {
                if (targetRow.style.display === 'table-row') {
                    // Collapse
                    targetRow.style.display = 'none';
                    row.classList.remove('is-open');
                } else {
                    // Expand
                    targetRow.style.display = 'table-row';
                    row.classList.add('is-open');
                }
            }
        });
    });

    // --- 3. Table Sorting Logic ---
    const tableBody = document.querySelector('.domain-table tbody');
    
    // Only run sorting code if the table exists
    if (tableBody) {
        const sortIcons = document.querySelectorAll('.sort-icon');
        let currentSort = { key: 'default', direction: 'asc' };

        // Function to get the value for sorting
        function getSortValue(row, key) {
            switch (key) {
                case 'domain-name':
                    return row.querySelector('td:nth-child(1) strong').innerText.toLowerCase();
                case 'ddns-status':
                    return row.querySelector('td:nth-child(2) span').innerText.toLowerCase();
                case 'ssl-expire':
                    const text = row.querySelector('td:nth-child(3)').innerText.trim();
                    if (text.includes('-')) return text; 
                    if (text === 'Missing') return '1000-01-01'; // Sorts Missing first
                    return '1000-01-02'; // Sorts N/A second
                case 'default':
                default:
                    return parseInt(row.dataset.defaultOrder, 10);
            }
        }

        // Main sort function
        function sortTable(sortKey) {
            let direction = 'asc';
            if (currentSort.key === sortKey && currentSort.direction === 'asc') {
                direction = 'desc';
            }
            
            currentSort.key = sortKey;
            currentSort.direction = direction;

            // Update icon classes
            sortIcons.forEach(icon => {
                icon.classList.remove('asc', 'desc');
                if (icon.dataset.sortKey === sortKey) {
                    icon.classList.add(direction);
                }
            });

            // Get all row pairs
            const rows = Array.from(tableBody.querySelectorAll('tr.domain-row'));
            const rowPairs = rows.map(row => ({
                main: row,
                details: row.nextElementSibling, // This is the expandable row
                sortValue: getSortValue(row, sortKey)
            }));

            // Sort the pairs
            rowPairs.sort((a, b) => {
                if (a.sortValue < b.sortValue) return direction === 'asc' ? -1 : 1;
                if (a.sortValue > b.sortValue) return direction === 'asc' ? 1 : -1;
                return 0;
            });

            // Re-append sorted pairs to the table body
            rowPairs.forEach(pair => {
                tableBody.appendChild(pair.main);
                if (pair.details) {
                    tableBody.appendChild(pair.details);
                }
            });
        }

        // Add click listeners to sort icons
        sortIcons.forEach(icon => {
            icon.addEventListener('click', () => {
                sortTable(icon.dataset.sortKey);
            });
        });
    }

});