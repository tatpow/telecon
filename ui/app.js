"use strict";

const $ = id => document.getElementById(id);
const ui = { alerts:{meeting_hidden:false,error:""}, lectures: [], scheduler: {running:false,active:false,message:"Подключение к приложению…"}, accountLogin:false, filter:"all", query:"", selectedDay:null, month:new Date(), ready:false, refreshing:false, listSignature:"", deleting:null };
const statuses = { scheduled:"Запланирована", running:"Подключение / запись", done:"Завершена", failed:"Ошибка", missed:"Пропущена", cancelled:"Остановлена" };
const filterLabels = { all:"Все лекции", upcoming:"Предстоящие", done:"Завершённые" };
const dayKey = value => { const d = value instanceof Date ? value : new Date(value); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; };
const localInput = date => `${dayKey(date)}T${String(date.getHours()).padStart(2,"0")}:${String(date.getMinutes()).padStart(2,"0")}`;
const timeLabel = value => new Date(value).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});
const field = (form,name) => form.elements.namedItem(name);
function el(tag, className, text) { const item=document.createElement(tag); if(className)item.className=className; if(text!==undefined)item.textContent=text; return item; }
function icon(name) { const svg=document.createElementNS("http://www.w3.org/2000/svg","svg"); svg.setAttribute("class","icon"); svg.setAttribute("aria-hidden","true"); const use=document.createElementNS("http://www.w3.org/2000/svg","use"); use.setAttribute("href",`#i-${name}`); svg.append(use); return svg; }
function showError(id,message) { $(id).textContent=message||""; $(id).classList.toggle("hidden",!message); }
let toastTimer;
function toast(message,error=false) { $("toast").textContent=message; $("toast").classList.toggle("error",error); $("toast").classList.remove("hidden"); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("toast").classList.add("hidden"),5500); }
async function call(method,...args) { if(!window.pywebview?.api)throw new Error("Нет связи с приложением. Запустите Телекон через app.py."); const result=await window.pywebview.api[method](...args); if(!result?.ok)throw new Error(result?.error||"Не удалось выполнить действие."); return result.data; }
async function action(button,handler) { if(button)button.disabled=true; try{await handler();}catch(error){toast(error.message,true);}finally{if(button)button.disabled=false;} }

function renderCalendar() {
  const month=new Date(ui.month.getFullYear(),ui.month.getMonth(),1);
  $("calendar-month").textContent=month.toLocaleDateString("ru-RU",{month:"long",year:"numeric"}).replace(" г.","");
  const first=new Date(month); first.setDate(first.getDate()-(first.getDay()+6)%7);
  const dates=new Set(ui.lectures.map(lecture=>lecture.starts_at.slice(0,10)));
  const fragment=document.createDocumentFragment();
  for(let index=0;index<42;index++) { const day=new Date(first); day.setDate(first.getDate()+index); const key=dayKey(day); const button=el("button","calendar-day",day.getDate()); button.type="button"; button.classList.toggle("outside",day.getMonth()!==month.getMonth()); button.classList.toggle("today",key===dayKey(new Date())); button.classList.toggle("chosen",key===ui.selectedDay); button.classList.toggle("has-lecture",dates.has(key)); button.setAttribute("aria-label",day.toLocaleDateString("ru-RU",{weekday:"long",day:"numeric",month:"long",year:"numeric"})); button.setAttribute("aria-pressed",String(key===ui.selectedDay)); button.addEventListener("click",()=>{ui.selectedDay=ui.selectedDay===key?null:key;renderCalendar();renderLectures();}); fragment.append(button); }
  $("calendar-days").replaceChildren(fragment); $("clear-day").classList.toggle("hidden",!ui.selectedDay);
}

function filteredLectures() { const now=new Date(); return ui.lectures.filter(lecture=>{if(ui.filter==="upcoming"&&!(lecture.status==="running"||(lecture.status==="scheduled"&&new Date(lecture.ends_at)>now)))return false;if(ui.filter==="done"&&lecture.status!=="done")return false;if(ui.selectedDay&&lecture.starts_at.slice(0,10)!==ui.selectedDay)return false;return `${lecture.title} ${lecture.url}`.toLocaleLowerCase("ru-RU").includes(ui.query);}).sort((a,b)=>a.starts_at.localeCompare(b.starts_at)||a.id-b.id); }
function dateSeparator(key) { const day=new Date(`${key}T12:00:00`); const today=dayKey(new Date()); const tomorrow=new Date();tomorrow.setDate(tomorrow.getDate()+1); const label=key===today?"Сегодня":key===dayKey(tomorrow)?"Завтра":day.toLocaleDateString("ru-RU",{weekday:"long"}); const separator=el("div","date-separator"); separator.append(el("span","date-day",label),el("span","date-description",day.toLocaleDateString("ru-RU",{day:"numeric",month:"long",year:"numeric"})),el("span","date-line"));return separator; }
function iconButton(name,label,handler) {const button=el("button","icon-button");button.type="button";button.title=label;button.setAttribute("aria-label",label);button.append(icon(name));button.addEventListener("click",handler);return button;}
function lectureCard(lecture) {
  const card=el("article",`lecture-card ${lecture.status}`); card.dataset.lectureId=String(lecture.id);
  const body=el("div","card-body");const time=el("div","card-time");time.append(el("div","start-time",timeLabel(lecture.starts_at)),el("div","end-time",timeLabel(lecture.ends_at)));
  const info=el("div","card-info");const top=el("div","card-topline");top.append(el("h2","card-title",lecture.title),el("span",`status-badge ${lecture.status}`,statuses[lecture.status]||lecture.status));const link=el("div","card-link");link.title=lecture.url;link.append(icon("link"),el("span","",lecture.url.replace(/^https:\/\//,"")));info.append(top,link);body.append(time,info);card.append(body);
  const bottom=el("div","card-bottom");const meta=el("div","card-meta");meta.append(icon("clock"),el("span","",lecture.status==="scheduled"?`Автоподключение за ${lecture.lead_minutes} мин.`:"Автовыключение микрофона и камеры"));
  const actions=el("div","card-actions");
  if(lecture.status==="scheduled")actions.append(iconButton("edit",`Редактировать «${lecture.title}»`,()=>openLecture(lecture)));
  if(lecture.status!=="running")actions.append(iconButton("trash",`Удалить «${lecture.title}»`,()=>{ui.deleting=lecture.id;$("delete-title").textContent=lecture.title;$("delete-dialog").showModal();}));
  if(lecture.status==="scheduled"&&new Date(lecture.ends_at)>new Date()) {const join=el("button","button join-button");join.type="button";join.append(icon("play"),el("span","","Подключиться и записать"));join.disabled=ui.scheduler.active||ui.accountLogin||ui.scheduler.closing;join.addEventListener("click",()=>action(join,async()=>{await call("join_now",lecture.id);await refresh();}));actions.append(join);}
  bottom.append(meta,actions);card.append(bottom);
  if(lecture.error)card.append(el("div","lecture-error",lecture.error));
  return card;
}
function renderLectures() {
  const lectures=filteredLectures();const fragment=document.createDocumentFragment();let previousDay=null;
  for(const lecture of lectures){const key=lecture.starts_at.slice(0,10);if(key!==previousDay){fragment.append(dateSeparator(key));previousDay=key;}fragment.append(lectureCard(lecture));}
  if(!lectures.length){const empty=el("div","empty-state");const image=el("div","empty-icon");image.append(icon(ui.query?"search":"calendar"));empty.append(image,el("h2","",ui.query?"Ничего не нашлось":ui.lectures.length?"На этот день пока нет лекций":"Начните с первой лекции"),el("p","",ui.query?"Попробуйте другое название или сбросьте фильтры.":ui.lectures.length?"Выберите другую дату или добавьте новую лекцию.":"Добавьте ссылку и время. Телекон подключится автоматически и сохранит видео через FFmpeg."));const create=el("button","button secondary",ui.query||ui.lectures.length?"Показать все лекции":"Добавить лекцию");create.type="button";create.addEventListener("click",()=>{if(ui.query||ui.lectures.length)resetFilters();else openLecture();});empty.append(create);fragment.append(empty);}
  $("lecture-list").replaceChildren(fragment);$("page-title").replaceChildren(document.createTextNode(filterLabels[ui.filter]),el("span","heading-count",lectures.length));
  $("list-label").textContent=ui.selectedDay?new Date(`${ui.selectedDay}T12:00:00`).toLocaleDateString("ru-RU",{day:"numeric",month:"long"}):"Лекции по датам";
  $("count-all").textContent=ui.lectures.length;$("count-upcoming").textContent=ui.lectures.filter(item=>item.status==="running"||(item.status==="scheduled"&&new Date(item.ends_at)>new Date())).length;$("count-done").textContent=ui.lectures.filter(item=>item.status==="done").length;
  document.querySelectorAll("[data-filter]").forEach(button=>{button.classList.toggle("selected",button.dataset.filter===ui.filter);button.setAttribute("aria-current",button.dataset.filter===ui.filter?"page":"false");});
}
function renderStatus() {
  $("hidden-window-warning").classList.toggle("hidden",!ui.alerts.meeting_hidden||ui.scheduler.closing);showError("alert-error",ui.alerts.error);
  const state=ui.scheduler; $("status-message").textContent=state.message;$("schedule-label").textContent=state.running?"Расписание активно":"Расписание на паузе";$("schedule-dot").classList.toggle("enabled",state.running);$("schedule-hint").textContent=ui.accountLogin?"Войдите в Яндекс в открытом браузере, затем закройте его.":state.running?"Подключимся вовремя. Оставьте приложение открытым.":"Включите, чтобы подключаться к лекциям автоматически.";
  $("schedule-toggle").replaceChildren(icon(state.running?"pause":"play"),el("span","",state.running?"Приостановить расписание":"Включить расписание"));$("schedule-toggle").disabled=ui.accountLogin||state.closing;
  $("live-panel").classList.toggle("hidden",!state.active&&!state.closing&&!ui.accountLogin);$("live-message").textContent=state.message;$("live-panel").querySelector("strong").textContent=ui.accountLogin?"Вход в Яндекс":state.closing?"Закрытие приложения":"Текущая лекция";$("stop-active").classList.toggle("hidden",!state.active||state.closing);
  if($("prepare-account"))$("prepare-account").disabled=ui.accountLogin||state.running||state.active||state.closing;
  $("quick-connect").disabled=ui.accountLogin||state.active||state.closing;
  $("start-quick").disabled=ui.accountLogin||state.active||state.closing||ui.quickSubmitting;
}
async function refresh() {
  if(ui.refreshing||!ui.ready)return;ui.refreshing=true;
  try{const state=await call("get_state");const signature=JSON.stringify([state.lectures,state.scheduler.active,state.scheduler.closing,state.account_login,Math.floor(Date.now()/60000)]);ui.lectures=state.lectures;ui.scheduler=state.scheduler;ui.alerts=state.alerts||{meeting_hidden:false,error:""};ui.accountLogin=Boolean(state.account_login);if(signature!==ui.listSignature){ui.listSignature=signature;renderCalendar();renderLectures();}renderStatus();showError("connection-error","");}catch(error){showError("connection-error",error.message);}finally{ui.refreshing=false;}
}
function resetFilters(){ui.filter="all";ui.query="";ui.selectedDay=null;$("search").value="";renderCalendar();renderLectures();}
async function openLecture(lecture=null) {
  let settings;try{settings=await call("get_settings");}catch(error){toast(error.message,true);return;}
  const form=$("lecture-form");form.reset();showError("lecture-error","");field(form,"id").value=lecture?.id||"";field(form,"title").value=lecture?.title||"";field(form,"url").value=lecture?.url||"";
  let start=new Date();start.setMinutes(start.getMinutes()+5,0,0);if(ui.selectedDay&&ui.selectedDay>dayKey(new Date()))start=new Date(`${ui.selectedDay}T09:00:00`);
  const end=new Date(start.getTime()+settings.default_duration_minutes*60000);field(form,"starts_at").value=lecture?lecture.starts_at.slice(0,16):localInput(start);field(form,"ends_at").value=lecture?lecture.ends_at.slice(0,16):localInput(end);field(form,"lead_minutes").value=lecture?.lead_minutes??2;
  form.dataset.durationMinutes=String(settings.default_duration_minutes);form.dataset.autoEnd=lecture?"":localInput(end);
  $("lecture-dialog-title").textContent=lecture?"Редактировать лекцию":"Новая лекция";$("save-lecture").textContent=lecture?"Сохранить изменения":"Добавить лекцию";$("lecture-dialog").showModal();setTimeout(()=>field(form,"title").focus(),30);
}
function settingsPayload(){const form=$("settings-form");return {recordings_dir:field(form,"recordings_dir").value.trim(),ffmpeg_path:field(form,"ffmpeg_path").value.trim(),prevent_sleep:field(form,"prevent_sleep").checked,default_duration_minutes:Number(field(form,"default_duration_minutes").value),alert_sound:field(form,"alert_sound").checked,alert_notification:field(form,"alert_notification").checked,alert_badge:field(form,"alert_badge").checked,alert_volume:Number(field(form,"alert_volume").value)};}
async function openSettings(){showError("settings-error","");$("recording-result").classList.add("hidden");try{const settings=await call("get_settings");const form=$("settings-form");form.reset();field(form,"recordings_dir").value=settings.recordings_dir;field(form,"ffmpeg_path").value=settings.ffmpeg_path;field(form,"prevent_sleep").checked=settings.prevent_sleep;field(form,"default_duration_minutes").value=settings.default_duration_minutes;for(const key of ["alert_sound","alert_notification","alert_badge"])field(form,key).checked=settings[key];field(form,"alert_volume").value=settings.alert_volume;renderAlertControls();$("settings-dialog").showModal();renderStatus();}catch(error){toast(error.message,true);}}

async function openQuick(){try{const settings=await call("get_settings");const form=$("quick-form");form.reset();showError("quick-error","");field(form,"ends_at").value=localInput(new Date(Date.now()+settings.default_duration_minutes*60000));$("quick-dialog").showModal();renderStatus();field(form,"url").focus();}catch(error){toast(error.message,true);}}
$("quick-connect").addEventListener("click",openQuick);
$("quick-form").addEventListener("submit",async event=>{event.preventDefault();if(ui.quickSubmitting)return;ui.quickSubmitting=true;renderStatus();showError("quick-error","");try{const form=event.currentTarget;await call("quick_join",{url:field(form,"url").value,ends_at:field(form,"ends_at").value});$("quick-dialog").close();resetFilters();toast("Подключаемся к встрече…");await refresh();}catch(error){showError("quick-error",error.message);}finally{ui.quickSubmitting=false;renderStatus();}});
field($("lecture-form"),"starts_at").addEventListener("input",()=>{const form=$("lecture-form");const end=field(form,"ends_at");const start=new Date(field(form,"starts_at").value);if(!field(form,"id").value&&end.value===form.dataset.autoEnd&&!Number.isNaN(start.getTime())){end.value=localInput(new Date(start.getTime()+Number(form.dataset.durationMinutes)*60000));form.dataset.autoEnd=end.value;}});

$("sidebar-create").addEventListener("click",()=>openLecture());$("composer-create").addEventListener("click",()=>openLecture());$("open-settings").addEventListener("click",openSettings);
document.querySelectorAll("[data-close]").forEach(button=>button.addEventListener("click",()=>$(button.dataset.close).close()));
document.querySelectorAll("dialog").forEach(dialog=>{dialog.addEventListener("click",event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}});});
document.querySelectorAll("[data-filter]").forEach(button=>button.addEventListener("click",()=>{ui.filter=button.dataset.filter;renderLectures();}));
$("search").addEventListener("input",event=>{ui.query=event.target.value.toLocaleLowerCase("ru-RU").trim();renderLectures();});
$("today-button").addEventListener("click",()=>{ui.month=new Date();ui.selectedDay=dayKey(new Date());renderCalendar();renderLectures();});$("calendar-month").addEventListener("click",()=>{ui.month=new Date();renderCalendar();});
$("prev-month").addEventListener("click",()=>{ui.month=new Date(ui.month.getFullYear(),ui.month.getMonth()-1,1);renderCalendar();});$("next-month").addEventListener("click",()=>{ui.month=new Date(ui.month.getFullYear(),ui.month.getMonth()+1,1);renderCalendar();});$("clear-day").addEventListener("click",()=>{ui.selectedDay=null;renderCalendar();renderLectures();});
$("schedule-toggle").addEventListener("click",event=>action(event.currentTarget,async()=>{await call("set_scheduler",{enabled:!ui.scheduler.running});await refresh();}));
$("stop-active").addEventListener("click",event=>action(event.currentTarget,async()=>{await call("stop_active");await refresh();}));
$("lecture-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget;const button=$("save-lecture");button.disabled=true;showError("lecture-error","");try{const payload={title:field(form,"title").value,url:field(form,"url").value,starts_at:field(form,"starts_at").value,ends_at:field(form,"ends_at").value,lead_minutes:Number(field(form,"lead_minutes").value)};if(field(form,"id").value)payload.id=Number(field(form,"id").value);await call("save_lecture",payload);$("lecture-dialog").close();toast(payload.id?"Изменения сохранены":"Лекция добавлена в расписание");await refresh();}catch(error){showError("lecture-error",error.message);}finally{button.disabled=false;}});
$("settings-form").addEventListener("submit",async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;showError("settings-error","");try{await call("save_settings",settingsPayload());$("settings-dialog").close();toast("Настройки сохранены");}catch(error){showError("settings-error",error.message);}finally{button.disabled=false;}});
$("check-recording").addEventListener("click",async event=>{const button=event.currentTarget;button.disabled=true;$("recording-result").textContent="Проверяем FFmpeg и захват звука процесса…";$("recording-result").classList.remove("hidden","error");showError("settings-error","");try{await call("save_settings",settingsPayload());const result=await call("check_recording");$("recording-result").textContent=`${result.version}\n${result.capture}\nПапка: ${result.recordings_dir}\nКомпоненты доступны. Захват проверяется при подключении к лекции.`;}catch(error){$("recording-result").textContent=error.message;$("recording-result").classList.add("error");}finally{button.disabled=false;}});

$("delete-form").addEventListener("submit",event=>{event.preventDefault();action(event.submitter,async()=>{await call("delete_lecture",ui.deleting);$("delete-dialog").close();ui.deleting=null;toast("Лекция удалена");await refresh();});});
document.querySelector(".brand").addEventListener("click",event=>{event.preventDefault();resetFilters();});
document.addEventListener("keydown",event=>{if(event.ctrlKey||event.metaKey||event.altKey||document.querySelector("dialog[open]")||/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName))return;if(event.key==="/"){event.preventDefault();$("search").focus();}if(event.key.toLowerCase()==="n"){event.preventDefault();openLecture();}});
async function connect(){if(ui.ready)return;ui.ready=true;await refresh();setInterval(refresh,1800);}
window.addEventListener("pywebviewready",connect);
$("today-date").textContent=new Date().toLocaleDateString("ru-RU",{weekday:"short",day:"numeric",month:"long",year:"numeric"}).replace(" г.","");
renderCalendar();renderLectures();if(window.pywebview?.api)connect();else setTimeout(()=>{if(!ui.ready)$("status-message").textContent="Откройте Телекон через app.py для работы с расписанием";},2500);

$("prepare-account").addEventListener("click",event=>action(event.currentTarget,async()=>{const url=field($("settings-form"),"account_url").value;await call("prepare_account",{url});$("settings-dialog").close();toast("Войдите в Яндекс в открытом браузере, затем закройте его");await refresh();}));

function renderAlertControls(){const form=$("settings-form");const volume=field(form,"alert_volume");$("alert-volume-label").textContent=`${volume.value}%`;volume.disabled=!field(form,"alert_sound").checked;$("preview-alert-sound").disabled=!field(form,"alert_sound").checked||Number(volume.value)===0;}
field($("settings-form"),"alert_volume").addEventListener("input",renderAlertControls);
field($("settings-form"),"alert_sound").addEventListener("change",renderAlertControls);
$("preview-alert-sound").addEventListener("click",event=>action(event.currentTarget,async()=>{await call("preview_alert_sound",{volume:Number(field($("settings-form"),"alert_volume").value)});}));
$("restore-meeting").addEventListener("click",event=>action(event.currentTarget,async()=>{await call("restore_meeting");await refresh();}));
