(function (root) {
  'use strict';
  const copy = value => structuredClone(value);
  const id = () => globalThis.crypto.randomUUID();
  const defaults = {step:0, imageId:null, name:'', kind:'anime', features:'', clothing:false,
    accessories:false, cropX:50, cropY:50, zoom:1, scale:65, offset:50,
    mood:'warm', genre:'rnb', accent:'amber', view:'watch'};
  function createState() { return {schema:1, draft:copy(defaults), versions:[]}; }
  function edit(state, patch) {
    const draft = {...state.draft};
    for (const key of Object.keys(defaults)) if (Object.hasOwn(patch,key)) draft[key] = patch[key];
    if (!Number.isInteger(draft.step) || draft.step < 0 || draft.step > 3) throw Error('步骤无效');
    for (const [key,min,max] of [['cropX',0,100],['cropY',0,100],['zoom',1,3],['scale',35,100],['offset',15,85]]) {
      if (!Number.isFinite(draft[key]) || draft[key]<min || draft[key]>max) throw Error('调整数值超出范围');
    }
    for (const [key,options] of Object.entries({kind:['anime','real'],mood:['warm','calm','bright'],
      genre:['rnb','house','pop','ambient'],accent:['amber','blue','sage'],view:['create','watch']})) {
      if (!options.includes(draft[key])) throw Error('选项无效');
    }
    for (const [key,limit] of [['name',60],['features',600]]) {
      if (typeof draft[key]!=='string' || draft[key].length>limit) throw Error('人物描述过长');
    }
    if (typeof draft.clothing!=='boolean' || typeof draft.accessories!=='boolean') throw Error('造型权限无效');
    if (draft.imageId!==null && (typeof draft.imageId!=='string' || !draft.imageId)) throw Error('图片引用无效');
    return {...state, draft};
  }
  function checkpoint(state, label) {
    const snapshot = {id:id(), label:String(label).slice(0,100), time:new Date().toISOString(), draft:copy(state.draft)};
    return {...state, versions:[...state.versions,snapshot]};
  }
  function restore(state, versionId) {
    const target = state.versions.find(version => version.id === versionId);
    if (!target) throw Error('找不到这个版本');
    const safe = checkpoint(state,'恢复前 · 自动保留');
    return {...safe, draft:copy(target.draft)};
  }
  function suggest(draft) {
    const moods = {
      warm:{scene:'林雨暖居',light:'窗外冷雨，室内琥珀暖光',color:'amber',motion:'克制、松弛',outfit:'柔软的深色针织或简洁休闲服'},
      calm:{scene:'静夜私邸',light:'低对比柔光，保留暗部材质',color:'blue',motion:'轻微、舒缓',outfit:'低饱和度、线条简洁的服装'},
      bright:{scene:'日落露台',light:'开阔暮色与局部金色光照',color:'sage',motion:'轻快、舒展',outfit:'轻便的日常服装与少量亮色'}
    };
    const genres = {rnb:'舒缓R&B适合松弛的局部律动',house:'House可突出稳定的节奏反馈，环境保持可读',
      pop:'流行音乐可随段落起伏调整表演强弱',ambient:'氛围音乐可减少动作，让空间留出呼吸感'};
    const mood = moods[draft.mood];
    return {...mood, outfit:draft.clothing?mood.outfit:null,
      accessory:draft.accessories?'保留标志性配饰，可尝试与场景呼应的耳机或局部色彩':null,
      reasons:[`先依据你希望表达的情绪，建议${mood.light}。`,`${genres[draft.genre]}。`,
        draft.features?`人物保持项：${draft.features}。`:'面容与人物身份保持；可补充标志性特征。'],
      status:'文字搭配建议；人物换装、空间替换与动作尚未制作'};
  }
  const api = {createState, edit, checkpoint, restore, suggest};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DeepmixAvatarModel = api;
})(globalThis);
