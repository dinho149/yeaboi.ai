// Shared copy-button behavior for any `.copy[data-copy]` element.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.copy[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var text = btn.getAttribute('data-copy');
      navigator.clipboard.writeText(text).then(function () {
        var prev = btn.textContent;
        btn.textContent = 'copied ✓';
        btn.classList.add('done');
        setTimeout(function () {
          btn.textContent = prev;
          btn.classList.remove('done');
        }, 1600);
      });
    });
  });
});
