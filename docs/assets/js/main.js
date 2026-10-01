/**
 * Quanifi Documentation Main JavaScript
 * Handles code copying, search filtering, mobile menu, and image lightboxes
 */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Mobile Menu Toggle
  const toggleBtn = document.querySelector('.mobile-menu-toggle');
  const mainMenu = document.querySelector('.main-menu');
  if (toggleBtn && mainMenu) {
    toggleBtn.addEventListener('click', () => {
      mainMenu.classList.toggle('active');
    });
  }

  // 2. Wrap all tables in .table-responsive if not already wrapped
  document.querySelectorAll('.main-content table').forEach(table => {
    if (!table.parentElement.classList.contains('table-responsive')) {
      const wrapper = document.createElement('div');
      wrapper.className = 'table-responsive';
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    }
  });

  // 3. Add Copy Button to Pre blocks
  document.querySelectorAll('pre').forEach(pre => {
    // Avoid duplicate buttons
    if (pre.querySelector('.copy-btn')) return;

    const copyBtn = document.createElement('button');
    copyBtn.className = 'copy-btn';
    copyBtn.innerText = 'Copy';
    copyBtn.title = 'Copy to clipboard';

    copyBtn.addEventListener('click', async () => {
      const code = pre.querySelector('code') ? pre.querySelector('code').innerText : pre.innerText;
      try {
        await navigator.clipboard.writeText(code);
        copyBtn.innerText = 'Copied!';
        copyBtn.style.background = '#10b981';
        setTimeout(() => {
          copyBtn.innerText = 'Copy';
          copyBtn.style.background = '';
        }, 2000);
      } catch (err) {
        copyBtn.innerText = 'Error';
        setTimeout(() => {
          copyBtn.innerText = 'Copy';
        }, 2000);
      }
    });

    pre.appendChild(copyBtn);
  });

  // 4. Sidebar Search Filter
  const searchInput = document.getElementById('docs-search-input');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase().trim();
      const menuItems = document.querySelectorAll('.widget-menu li');
      
      menuItems.forEach(item => {
        const text = item.textContent.toLowerCase();
        if (q === '' || text.includes(q)) {
          item.style.display = '';
        } else {
          item.style.display = 'none';
        }
      });
    });
  }

  // 5. Image Lightbox for Canvas Screenshots
  const modal = document.createElement('div');
  modal.className = 'lightbox-modal';
  modal.innerHTML = `
    <span class="lightbox-close" title="Close (Esc)">&times;</span>
    <div style="display: flex; flex-direction: column; align-items: center; max-width: 96vw; max-height: 94vh;">
      <img class="lightbox-content" src="" alt="Enlarged Canvas View">
      <div style="margin-top: 12px; display: flex; gap: 14px; align-items: center;">
        <a class="lightbox-full-link" href="" target="_blank" rel="noopener" style="color: #ffffff; background: #01a1c0; padding: 6px 16px; border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: 700; box-shadow: 0 2px 8px rgba(0,0,0,0.4); display: inline-flex; align-items: center; gap: 6px;">
          <span>🔍 View Full Resolution Image</span>
          <span>&nearr;</span>
        </a>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const modalImg = modal.querySelector('.lightbox-content');
  const modalClose = modal.querySelector('.lightbox-close');
  const fullLink = modal.querySelector('.lightbox-full-link');

  modalClose.addEventListener('click', () => {
    modal.classList.remove('active');
  });

  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      modal.classList.remove('active');
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.classList.contains('active')) {
      modal.classList.remove('active');
    }
  });

  document.querySelectorAll('.gallery-card img, .main-content img').forEach(img => {
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', () => {
      modalImg.src = img.src;
      if (fullLink) fullLink.href = img.src;
      modal.classList.add('active');
    });
  });
});
