window.closeFullscreenMap = function() {
  console.log('Close map clicked');
  document.body.classList.remove('fullscreen-map-mode');
  document.querySelectorAll('.map-card').forEach(card => card.classList.remove('hidden'));
  const btn = document.getElementById('showAllMapsBtn');
  if (btn) btn.style.display = 'none';
  selectTab(window.lastActiveTabBeforeMap || 'alerts');
  window.scrollTo({ top: 0 });
  setTimeout(() => { Object.values(maps).forEach(m => m?.invalidateSize()); }, 200);
};

  // Poll gateway status periodically
  setInterval(checkGatewayStatus, 30000);
});

document.addEventListener('DOMContentLoaded', async () => {
  await populateDropdown('filterZone', 'zones', null, 'All Zones');
  await populateDropdown('filterDivision', 'divisions', null, 'All Divisions');
  await populateDropdown('filterSection', 'sections', null, 'All Sections');
  
  document.getElementById('filterZone')?.addEventListener('change', async (e) => {
    const val = e.target.value;
    await populateDropdown('filterDivision', 'divisions', val || null, 'All Divisions');
    await populateDropdown('filterSection', 'sections', null, 'All Sections');
    state.filterZone = val || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  
  document.getElementById('filterDivision')?.addEventListener('change', async (e) => {
    const val = e.target.value;
    await populateDropdown('filterSection', 'sections', val || null, 'All Sections');
    state.filterDivision = val || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  
  document.getElementById('filterSection')?.addEventListener('change', (e) => {
    state.filterSection = e.target.value || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
});











