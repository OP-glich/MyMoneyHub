'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '..', 'moneyhub', 'static', 'hub.js'), 'utf8');
async function check(failClipboard, fallbackSuccess = true, card = false) {
  const handlers = {};
  const panel = {tagName:'DETAILS',open:false};
  let printed = 0, copied = null, selected = false;
  const message = {value:'OSI LOAN\nBorrower: Demo Borrower A\nPurpose: School supplies\nPrincipal: N$ 1,000.00\nInterest rate: 20% fixed for the loan term\nInterest: N$ 200.00\nTotal repayment: N$ 1,200.00\nDate issued: 2026-11-13\nRepayment due: 2026-12-13',
    closest:()=>panel,focus(){},select(){selected=true;}};
  const status = {textContent:''};
  const printButton = {addEventListener:(_,f)=>handlers.print=f};
  const copyButton = {dataset:card?{copyTarget:'loan-message-42',copyStatus:'copy-status-42'}:{},addEventListener:(_,f)=>handlers.copy=f};
  const link = {dataset:{open:'test-panel'},addEventListener:(_,f)=>handlers.open=f};
  const document = {
    querySelectorAll(selector) {
      return ({'[data-print]':[printButton],'[data-copy-message]':[copyButton],'[data-open]':[link]})[selector] || [];
    },
    getElementById(id) {return {'whatsapp-message':message,'copy-status':status,'loan-message-42':message,'copy-status-42':status,'test-panel':panel}[id];},
    addEventListener(){}, execCommand(command){assert.equal(command,'copy');return fallbackSuccess;}
  };
  vm.runInNewContext(code,{document,window:{print(){printed++;},addEventListener(){}},
    navigator:{clipboard:{async writeText(text){if(failClipboard)throw Error('Denied');copied=text;}}},
    location:{hash:''},console,setTimeout,clearTimeout,URLSearchParams,AbortController});
  handlers.print(); assert.equal(printed,1);
  handlers.open(); assert.equal(panel.open,true);
  panel.open=false;
  await handlers.copy();
  if(failClipboard){assert.equal(panel.open,true);assert.equal(selected,true);}
  else {assert.equal(copied,message.value);}
  assert.ok(status.textContent.includes(fallbackSuccess ? 'Message copied' : 'Ctrl+C'));
}
function submitChecks() {
  const handlers={};
  const button={textContent:'Save Loan',dataset:{},disabled:false,setAttribute(){},removeAttribute(){}};
  const form={dataset:{},method:'post',action:'/action/loan_plan',querySelector:()=>button};
  const document={querySelectorAll:s=>s==='button[data-original-label]'?[button]:s==='form[data-submitting]'?[form]:[],
    addEventListener:(type,fn)=>handlers[type]=fn};
  vm.runInNewContext(code,{document,window:{addEventListener:(type,fn)=>handlers[type]=fn},location:{hash:''},setTimeout,clearTimeout});
  let prevented=false;
  const event={target:form,submitter:button,preventDefault(){prevented=true;}};
  handlers.submit(event);
  assert.equal(button.disabled,false,'submitter must remain successful for native form submission');
  assert.equal(prevented,false);
  handlers.submit(event);
  assert.equal(prevented,true,'second click must be suppressed');
  handlers.pageshow();
  assert.equal(form.dataset.submitting,'');
  assert.equal(button.textContent,'Save Loan');
}
(async()=>{await check(false);await check(true);await check(true,false);await check(false,true,true);await check(true,true,true);submitChecks();console.log('print, clipboard, fallback and disclosure passed');})().catch(error=>{console.error(error);process.exitCode=1;});
