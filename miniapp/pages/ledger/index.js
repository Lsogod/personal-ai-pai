const { getToken } = require("../../utils/auth");
const { fetchLedgers, fetchLedgerStats, createLedger, updateLedger, deleteLedger } = require("../../utils/http");

function fmtSafe(iso) {
  return String(iso || "").replace(/-/g, "/").replace("T", " ").replace("Z", " +00:00");
}
function fmtDateTime(iso) {
  if (!iso) return "";
  const d = new Date(fmtSafe(iso));
  if (Number.isNaN(d.getTime())) return "";
  const pad = n => (n+"").padStart(2,"0");
  return pad(d.getMonth()+1)+"-"+pad(d.getDate())+" "+pad(d.getHours())+":"+pad(d.getMinutes());
}
function todayISO() {
  const d = new Date();
  const pad = n => (n+"").padStart(2,"0");
  return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate())+"T"+pad(d.getHours())+":"+pad(d.getMinutes());
}

const CATS = ["餐饮","交通","购物","居家","娱乐","医疗","教育","其他"];
const CAT_CLR = {"餐饮":"#4F6EF7","交通":"#34D399","购物":"#F59E0B","居家":"#8B5CF6","娱乐":"#EC4899","医疗":"#EF4444","教育":"#06B6D4","其他":"#94A3B8"};
function catClr(c){return CAT_CLR[c]||"#94A3B8";}

function buildCatStats(rows){
  const m={};let total=0;
  for(const r of rows){const c=r.category||"其他",a=parseFloat(r.amount)||0;m[c]=(m[c]||0)+a;total+=a;}
  const entries=Object.entries(m).sort((a,b)=>b[1]-a[1]).map(([name,value])=>({
    name,value:Math.round(value*100)/100,
    percent:total>0?Math.round(value/total*1000)/10:0,
    color:catClr(name),
  }));
  return{total:Math.round(total*100)/100,entries};
}

function buildDailyTrend(rows){
  const m={};
  for(const r of rows){
    const dt=new Date(fmtSafe(r.transaction_date));
    if(Number.isNaN(dt.getTime()))continue;
    const k=(dt.getMonth()+1)+"/"+dt.getDate();
    m[k]=(m[k]||0)+(parseFloat(r.amount)||0);
  }
  const days=[],now=new Date();
  for(let i=13;i>=0;i--){
    const d=new Date(now.getFullYear(),now.getMonth(),now.getDate()-i);
    const k=(d.getMonth()+1)+"/"+d.getDate();
    days.push({label:k,value:Math.round((m[k]||0)*100)/100});
  }
  return days;
}

Page({
  data:{
    authed:false,loading:false,
    stats:{total:0,count:0},ledgers:[],
    catStats:{total:0,entries:[]},dailyTrend:[],
    avgDaily:0,maxDay:{label:"-",value:0},
    activeTab:"overview",
    cats:CATS,
    // form
    showForm:false, formMode:"add", formId:null,
    formAmount:"", formItem:"", formCategory:"其他", formDate:"",
  },
  onLoad(){this._dpr=wx.getWindowInfo().pixelRatio||2;},
  onShow(){
    const authed=!!getToken();this.setData({authed});
    if(!authed){this.setData({stats:{total:0,count:0},ledgers:[],catStats:{total:0,entries:[]},dailyTrend:[]});return;}
    this.loadData();
  },
  async loadData(){
    this.setData({loading:true});
    try{
      const[stats,ledgers]=await Promise.all([fetchLedgerStats(30),fetchLedgers(100)]);
      const rows=(Array.isArray(ledgers)?ledgers:[]).map(r=>({...r,_time:fmtDateTime(r.transaction_date),_catClr:catClr(r.category)}));
      const catStats=buildCatStats(rows);
      const dailyTrend=buildDailyTrend(rows);
      const vals=dailyTrend.map(d=>d.value).filter(v=>v>0);
      const avgDaily=vals.length?Math.round(vals.reduce((a,b)=>a+b,0)/14*100)/100:0;
      const maxDay=dailyTrend.reduce((a,b)=>b.value>a.value?b:a,{label:"-",value:0});
      this.setData({stats:stats||{total:0,count:0},ledgers:rows,catStats,dailyTrend,avgDaily,maxDay},()=>{
        if(this.data.activeTab==="overview"){this.drawPie();this.drawTrend();}
      });
    }catch(err){wx.showToast({title:err.message||"加载失败",icon:"none"});}
    finally{this.setData({loading:false});}
  },
  onSwitchTab(e){
    const tab=e.currentTarget.dataset.tab;
    this.setData({activeTab:tab},()=>{
      if(tab==="overview")setTimeout(()=>{this.drawPie();this.drawTrend();},60);
    });
  },

  /* ── Form ── */
  onShowAdd(){
    this.setData({showForm:true,formMode:"add",formId:null,formAmount:"",formItem:"",formCategory:"其他",formDate:todayISO()});
  },
  onShowEdit(e){
    const item=e.currentTarget.dataset.item;
    if(!item)return;
    const rawDate=(item.transaction_date||"").replace("Z","").slice(0,16);
    this.setData({showForm:true,formMode:"edit",formId:item.id,formAmount:String(item.amount||""),formItem:item.item||"",formCategory:item.category||"其他",formDate:rawDate||todayISO()});
  },
  onCloseForm(){this.setData({showForm:false});},
  onFormAmount(e){this.setData({formAmount:e.detail.value});},
  onFormItem(e){this.setData({formItem:e.detail.value});},
  onFormCat(e){this.setData({formCategory:CATS[e.detail.value]||"其他"});},
  onFormDate(e){this.setData({formDate:e.detail.value});},
  onFormTime(e){
    const cur=this.data.formDate||"";
    const datePart=cur.slice(0,10)||todayISO().slice(0,10);
    this.setData({formDate:datePart+"T"+e.detail.value});
  },

  async onFormSubmit(){
    const amt=parseFloat(this.data.formAmount);
    if(!amt||amt<=0){wx.showToast({title:"请输入金额",icon:"none"});return;}
    const data={
      amount:amt,
      item:this.data.formItem||"手动记录",
      category:this.data.formCategory||"其他",
    };
    if(this.data.formDate)data.transaction_date=this.data.formDate+":00Z";

    try{
      if(this.data.formMode==="add"){
        await createLedger(data);
        wx.showToast({title:"添加成功",icon:"success"});
      }else{
        await updateLedger(this.data.formId,data);
        wx.showToast({title:"修改成功",icon:"success"});
      }
      this.setData({showForm:false});
      this.loadData();
    }catch(err){wx.showToast({title:err.message||"操作失败",icon:"none"});}
  },

  onDeleteLedger(e){
    const item=e.currentTarget.dataset.item;
    if(!item)return;
    wx.showModal({
      title:"确认删除",
      content:"删除「"+(item.item||"账单")+"」¥"+item.amount+"？",
      confirmColor:"#EF4444",
      success:async(res)=>{
        if(!res.confirm)return;
        try{
          await deleteLedger(item.id);
          wx.showToast({title:"已删除",icon:"success"});
          this.loadData();
        }catch(err){wx.showToast({title:err.message||"删除失败",icon:"none"});}
      }
    });
  },

  /* ── Charts ── */
  drawPie(){
    this.createSelectorQuery().select("#pieCanvas").fields({node:true,size:true}).exec(res=>{
      if(!res||!res[0]||!res[0].node)return;
      const c=res[0].node,ctx=c.getContext("2d"),dpr=this._dpr,w=res[0].width,h=res[0].height;
      c.width=w*dpr;c.height=h*dpr;ctx.scale(dpr,dpr);this._pie(ctx,w,h);
    });
  },
  _pie(ctx,w,h){
    const{entries,total}=this.data.catStats;
    const cx=w/2,cy=h/2,r=Math.min(cx,cy)-6,ir=r*0.58;
    ctx.clearRect(0,0,w,h);
    if(!entries.length){
      ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.strokeStyle="#e5e7eb";ctx.lineWidth=2;ctx.stroke();
      ctx.fillStyle="#9ca3af";ctx.font="13px -apple-system,sans-serif";ctx.textAlign="center";ctx.textBaseline="middle";
      ctx.fillText("暂无数据",cx,cy);return;
    }
    let a=-Math.PI/2;
    for(const e of entries){const sw=(e.value/total)*Math.PI*2;ctx.beginPath();ctx.moveTo(cx,cy);ctx.arc(cx,cy,r,a,a+sw);ctx.closePath();ctx.fillStyle=e.color;ctx.fill();a+=sw;}
    ctx.beginPath();ctx.arc(cx,cy,ir,0,Math.PI*2);ctx.fillStyle="#fff";ctx.fill();
    ctx.fillStyle="#111827";ctx.font="bold 17px -apple-system,sans-serif";ctx.textAlign="center";ctx.textBaseline="middle";
    ctx.fillText("¥"+total,cx,cy-6);
    ctx.fillStyle="#6b7280";ctx.font="11px -apple-system,sans-serif";ctx.fillText("总支出",cx,cy+12);
  },
  drawTrend(){
    this.createSelectorQuery().select("#trendCanvas").fields({node:true,size:true}).exec(res=>{
      if(!res||!res[0]||!res[0].node)return;
      const c=res[0].node,ctx=c.getContext("2d"),dpr=this._dpr,w=res[0].width,h=res[0].height;
      c.width=w*dpr;c.height=h*dpr;ctx.scale(dpr,dpr);this._trend(ctx,w,h);
    });
  },
  _trend(ctx,w,h){
    const data=this.data.dailyTrend;
    const pL=34,pR=10,pT=14,pB=28,cW=w-pL-pR,cH=h-pT-pB;
    ctx.clearRect(0,0,w,h);
    const vals=data.map(d=>d.value),mx=Math.max(...vals,1);
    ctx.strokeStyle="#f0f0f5";ctx.lineWidth=0.5;
    for(let i=0;i<=4;i++){const y=pT+cH/4*i;ctx.beginPath();ctx.moveTo(pL,y);ctx.lineTo(w-pR,y);ctx.stroke();}
    ctx.fillStyle="#9ca3af";ctx.font="9px -apple-system,sans-serif";ctx.textAlign="right";ctx.textBaseline="middle";
    for(let i=0;i<=4;i++){const y=pT+cH/4*i;ctx.fillText(Math.round(mx*(1-i/4))+"",pL-5,y);}
    if(data.length<2)return;
    const sx=cW/(data.length-1);
    const pts=data.map((d,i)=>({x:pL+i*sx,y:pT+cH-d.value/mx*cH}));
    const grd=ctx.createLinearGradient(0,pT,0,pT+cH);
    grd.addColorStop(0,"rgba(79,110,247,0.15)");grd.addColorStop(1,"rgba(79,110,247,0.01)");
    ctx.beginPath();ctx.moveTo(pts[0].x,pT+cH);for(const p of pts)ctx.lineTo(p.x,p.y);
    ctx.lineTo(pts[pts.length-1].x,pT+cH);ctx.closePath();ctx.fillStyle=grd;ctx.fill();
    ctx.beginPath();ctx.moveTo(pts[0].x,pts[0].y);for(let i=1;i<pts.length;i++)ctx.lineTo(pts[i].x,pts[i].y);
    ctx.strokeStyle="#4F6EF7";ctx.lineWidth=2;ctx.lineJoin="round";ctx.stroke();
    for(const p of pts){ctx.beginPath();ctx.arc(p.x,p.y,2.5,0,Math.PI*2);ctx.fillStyle="#4F6EF7";ctx.fill();}
    ctx.fillStyle="#9ca3af";ctx.font="9px -apple-system,sans-serif";ctx.textAlign="center";ctx.textBaseline="top";
    for(let i=0;i<data.length;i++){if(i%3===0||i===data.length-1)ctx.fillText(data[i].label,pts[i].x,pT+cH+6);}
  },
  onGoLogin(){wx.navigateTo({url:"/pages/login/index?redirect="+encodeURIComponent("/pages/ledger/index")});}
});
