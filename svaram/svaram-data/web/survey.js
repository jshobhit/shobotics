// Connect the existing questionnaire to the shared registry; never use browser storage.
(()=>{
const form=document.getElementById('survey'),status=document.getElementById('status');
const sid=new URLSearchParams(location.search).get('session'),kind=location.pathname==='/post'?'post':'pre';
const token=document.querySelector('meta[name=api-token]').content;
let version=0,timer,loaded=false,changed=false,serial=0,answerTimes={},createdAt=new Date().toISOString(),queue=Promise.resolve(),pendingSubmission=null,blocked=false;
const originalDisabled=new Set([...form.elements].filter(e=>e.disabled));
function say(text,error=false){status.textContent=text;status.classList.toggle('error',error)}
async function api(path,data){const response=await fetch(path,{method:data?'POST':'GET',headers:{'Content-Type':'application/json','X-Study-Token':token},body:data?JSON.stringify(data):undefined});const result=await response.json();if(!response.ok){if(response.status===409)blocked=true;throw Error(result.error)}return result}
function collect(){const answers={};for(const el of form.elements){if(!el.name||['participant_id','session_id'].includes(el.name))continue;if(el.disabled){answers[el.name]=null;continue}if(el.type==='radio'){if(!(el.name in answers))answers[el.name]=null;if(el.checked)answers[el.name]=el.name.startsWith('state_')?Number(el.value):el.value}else if(el.type==='checkbox'){if(!Array.isArray(answers[el.name]))answers[el.name]=[];if(el.checked)answers[el.name].push(el.value)}else{answers[el.name]=el.value.trim()===''?null:el.type==='number'?Number(el.value):el.value.trim()}}for(const key in answers)if(Array.isArray(answers[key])&&!answers[key].length)answers[key]=null;return answers}
function capture(){return {answers:collect(),answer_times:{...answerTimes},created_at_utc:createdAt,updated_at_utc:new Date().toISOString(),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone}}
function save(submit=false){clearTimeout(timer);if(!loaded||blocked)return Promise.resolve();if(submit&&!form.reportValidity())return Promise.resolve();const generation=serial;const snapshot=capture();queue=queue.catch(()=>{}).then(async()=>{if(blocked)return;if(pendingSubmission&&!submit){say('Submission status is unknown. Click Submit answers to retry before saving further edits.',true);return}const request={session_id:sid,kind,data:snapshot,version,submit};let savedGeneration=generation;if(submit){if(!pendingSubmission)pendingSubmission={request:{...request,request_id:crypto.randomUUID()},generation};Object.assign(request,pendingSubmission.request);savedGeneration=pendingSubmission.generation}say('Saving…');try{const result=await api('/api/assessment',request);version=result.version;if(submit)pendingSubmission=null;if(serial===savedGeneration){changed=false;if(kind==='pre')dirty=false}say(submit?`Answers submitted. Revision ${result.revision} saved.`:'Draft saved to the study registry.');if(changed){say('Previous answers saved. Saving your newer edits…');timer=setTimeout(()=>save().catch(()=>{}),650)}}catch(error){changed=true;say('Not saved: '+error.message,true);throw error}});return queue}
function edited(event){if(!loaded)return;const name=event.target.name||event.target.closest('[data-question]')?.dataset.question;if(name)answerTimes[name]=new Date().toISOString();serial++;changed=true;clearTimeout(timer);timer=setTimeout(()=>save().catch(()=>{}),650)}
form.addEventListener('input',edited);
form.addEventListener('click',event=>{if(event.target.closest('.reset-answer')){setTimeout(()=>edited(event),0)}});
form.addEventListener('submit',event=>{event.preventDefault();event.stopImmediatePropagation();if(pendingSubmission)say('Retrying the previous submission.');save(true).catch(()=>{})},true);
document.getElementById('save').addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation();save().catch(()=>{})},true);
const clear=document.getElementById('clear');if(clear){clear.hidden=true;clear.addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation()},true)}
if(kind==='pre'){
const panel=document.querySelector('.export-panel');panel.querySelector('h2').textContent='Save your answers.';panel.querySelector('button.primary').textContent='Submit answers';panel.querySelectorAll('p.small,details').forEach(el=>el.hidden=true);const note=document.createElement('p');note.textContent='Drafts save automatically to this study visit. Submit when you have finished. Corrections create a new revision.';panel.prepend(note);
document.querySelector('#context .section-head p').textContent='The study app supplies your participant and visit codes.';
document.querySelector('.notice').textContent='You can skip any health question. This form is for research and does not diagnose health problems.';
const back=document.createElement('a');back.href='/';back.textContent='← Study visits';document.querySelector('main').prepend(back);
for(const id of ['participant_id','session_id']){const el=document.getElementById(id);el.readOnly=true;el.nextElementSibling.textContent='Supplied by the study app.'}
}
// Prevent typing into a blank form before its saved draft has loaded.
for(const el of form.elements)el.disabled=true;
api(`/api/assessment?session=${encodeURIComponent(sid||'')}&kind=${kind}`).then(result=>{
for(const el of form.elements)el.disabled=originalDisabled.has(el);
version=result.version;const data=result.data;if(data){answerTimes=data.answer_times||{};createdAt=data.created_at_utc||createdAt;for(const el of form.elements){if(!el.name)continue;const value=data.answers[el.name];if(el.type==='radio')el.checked=value!==null&&value!==undefined&&String(value)===el.value;else if(el.type==='checkbox')el.checked=Array.isArray(value)&&value.includes(el.value);else el.value=value??''}}
if(kind==='pre'){document.getElementById('participant_id').value=result.session.code;document.getElementById('session_id').value=result.session.id;conditional();progress()}else document.getElementById('identity').textContent=`${result.session.code} · Visit ${result.session.visit}`;
loaded=true;say(result.submitted?`Revision ${result.revision} is submitted. Further submissions create a new revision.`:'Ready. Drafts save automatically.');
}).catch(error=>say(error.message,true));
window.addEventListener('beforeunload',event=>{if(changed){event.preventDefault();event.returnValue=''}});
})();
