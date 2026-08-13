// Load-more for the card grid.
//
// The server keeps rendering the tiles (/cards?...&fragment=1 returns the same
// _card_tiles.html partial the first batch came from), so this file never needs
// to know how a card is drawn — it only appends HTML and tracks which batch is
// next. Everything it needs is on #card-grid's data-* attributes, put there by
// filter_url(), which already folds the active filters into the URL.
//
// Progressive enhancement: with scripts off the server-rendered pagination is
// the whole story. This replaces it only once it has successfully taken over.
(function () {
  'use strict';

  var grid = document.getElementById('card-grid');
  if (!grid || !window.fetch) return;

  // dataset.batch, not dataset.page — see the note in cards.html.
  var page = parseInt(grid.dataset.batch, 10);
  var totalPages = parseInt(grid.dataset.totalPages, 10);
  var perPage = parseInt(grid.dataset.perPage, 10);
  var totalCards = parseInt(grid.dataset.totalCards, 10);
  var nextUrl = grid.dataset.nextUrl;
  if (!(totalPages > page)) return;

  // Only hide the real pagination once we know we can do the job.
  var pagination = document.getElementById('card-pagination');
  if (pagination) pagination.hidden = true;

  var bar = document.createElement('div');
  bar.className = 'load-more';
  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn';
  var status = document.createElement('p');
  status.className = 'text-sm text-muted';
  // Politely announced so a screen reader hears the count change rather than
  // silently gaining several hundred links.
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  bar.appendChild(button);
  bar.appendChild(status);
  grid.parentNode.insertBefore(bar, grid.nextSibling);

  function shown() {
    return Math.min(page * perPage, totalCards);
  }

  function render() {
    var left = totalCards - shown();
    button.textContent = 'Load ' + Math.min(perPage, left) + ' more';
    status.textContent = 'Showing ' + shown() + ' of ' + totalCards;
  }

  function finish() {
    button.remove();
    status.textContent = 'Showing all ' + totalCards;
  }

  var loading = false;

  function load() {
    if (loading || page >= totalPages) return;
    loading = true;
    button.disabled = true;
    button.textContent = 'Loading…';

    fetch(nextUrl, { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (html) {
        var slot = document.createElement('div');
        slot.innerHTML = html;
        // Move nodes rather than concatenating innerHTML: rewriting the grid's
        // markup would rebuild every tile already on screen and restart each
        // image load.
        while (slot.firstChild) grid.appendChild(slot.firstChild);

        page += 1;
        nextUrl = nextUrl.replace(/([?&]page=)\d+/, '$1' + (page + 1));
        loading = false;
        button.disabled = false;
        if (page >= totalPages) finish(); else render();
      })
      .catch(function () {
        // Put the user back on the path that definitely works.
        loading = false;
        button.disabled = false;
        button.textContent = 'Load more';
        status.textContent = 'Could not load more cards.';
        if (pagination) pagination.hidden = false;
      });
  }

  button.addEventListener('click', load);
  render();
}());
