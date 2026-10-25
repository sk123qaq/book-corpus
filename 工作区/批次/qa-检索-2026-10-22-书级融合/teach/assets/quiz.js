document.querySelectorAll('.quiz').forEach(function (quiz) {
  var answer = quiz.dataset.answer;
  var hint = quiz.dataset.hint || '回到上面的表再看一眼。';
  quiz.querySelector('button').addEventListener('click', function () {
    var picked = quiz.querySelector('input:checked');
    var fb = quiz.querySelector('.fb');
    if (!picked) { fb.textContent = '先选一个'; fb.className = 'fb'; return; }
    var ok = picked.value === answer;
    fb.textContent = ok ? '对。' : '不对，答案是另一项。' + hint;
    fb.className = 'fb ' + (ok ? 'ok' : 'no');
  });
});
