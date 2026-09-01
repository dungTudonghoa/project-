(function(){
  function scoreRadios(formId, answers, resultId){
    const form=document.getElementById(formId); if(!form) return;
    const result=document.getElementById(resultId);
    const btn=form.querySelector('[data-check]'); const reset=form.querySelector('[data-reset]');
    if(btn) btn.addEventListener('click',()=>{let score=0;Object.entries(answers).forEach(([name,ans])=>{const picked=form.querySelector(`input[name="${name}"]:checked`); if(picked&&picked.value===ans) score++;}); result.textContent=`Đúng ${score}/${Object.keys(answers).length} câu`; result.className='result '+(score===Object.keys(answers).length?'correct':'');});
    if(reset) reset.addEventListener('click',()=>{form.reset(); result.textContent='';});
  }
  scoreRadios('l1-ex1',{l1q1:'by',l1q2:'on',l1q3:'in',l1q4:'near',l1q5:'in',l1q6:'by',l1q7:'in'},'l1-ex1-result');
  scoreRadios('l2-ex1',{q1:'13th',q2:'Crawford',q3:'0870 292720',q4:'70 Sidney'},'l2-ex1-result');
  const gapForm=document.getElementById('l1-ex2'); if(gapForm){const ans={l1gap8:'reputation',l1gap9:'crime',l1gap10:'conclude',l1gap11:'impact',l1gap12:'influence',l1gap13:'industrial',l1gap14:'surround',l1gap15:'ranks',l1gap16:'reaction',l1gap17:'diverse',l1gap18:'locals',l1gap19:'lifestyle'}; const r=document.getElementById('l1-ex2-result'); gapForm.querySelector('[data-check]').addEventListener('click',()=>{let s=0;Object.entries(ans).forEach(([n,a])=>{const el=gapForm.querySelector(`[name="${n}"]`);if(el&&el.value===a)s++});r.textContent=`Đúng ${s}/12 ô`;r.className='result '+(s===12?'correct':'')});gapForm.querySelector('[data-reset]').addEventListener('click',()=>{gapForm.reset();r.textContent=''})}
  const audio=document.getElementById('exercise1-audio'), picker=document.getElementById('audio-file-input'), status=document.getElementById('audio-status');
  if(audio&&picker){audio.addEventListener('error',()=>{if(status)status.textContent='Chưa có file MP3 trong bộ web. Bấm “Chọn MP3 Exercise 1” để chọn file bạn đã tải.'}); picker.addEventListener('change',()=>{const f=picker.files&&picker.files[0];if(!f)return;audio.src=URL.createObjectURL(f);audio.load();if(status)status.textContent='Đã nạp MP3 từ máy của bạn.'})}
})();