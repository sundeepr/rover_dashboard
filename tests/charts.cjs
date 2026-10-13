// Exercise rendering with a server window and no browser-side sampling.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {textContent: '', attrs: {}, setAttribute(k,v) {this.attrs[k]=v;}, classList: {toggle(){}}});
  return elements.get(id);
}};
const context = vm.createContext({document, URL, window: {location: {origin: 'http://rover'}}});
vm.runInContext(fs.readFileSync('app.js','utf8').split('// Dashboard startup')[0], context);
vm.runInContext(`
state.telemetry.history = {now: 300000, windowMs: 300000, samples: [
 {time: 0, battery: {Capacity:74,Current:-50,Power:-22}},
 {time: 2000, battery: {Capacity:74,Current:50,Power:22}},
 {time: 4000, battery: {Capacity:null,Current:null,Power:null}},
 {time: 6000, battery: {Capacity:73,Current:65,Power:30}}
]};
const battery = {available:true, summary:{capacityRemaining:'73%',current:'65 A',power:'30 W'}};
renderBatteryHistory(battery);
`, context);
assert.match(elements.get('batteryCurrentRange').textContent, /^Scale: -50 to 50 A/);
assert.match(elements.get('batteryCurrentRange').textContent, /clipped/);
assert.equal(elements.get('batteryCurrentLatest').textContent, '65.0 A');
const path = elements.get('batteryCurrentPath').attrs.d;
assert.match(path, /^M 8.00 132.00 L 10.56 8.00 M 15.68 8.00/);
assert.ok(elements.get('batteryPowerPath').attrs.d.length);
vm.runInContext('renderBatteryHistory(battery);',context);
assert.equal(elements.get('batteryCurrentPath').attrs.d, path);
assert.equal(vm.runInContext('state.telemetry.history.samples.length',context),4);
console.log('Chart rendering checks passed');

vm.runInContext(`
state.telemetry.history.now = 172800000;
state.telemetry.history.capacity = {windowMs:172800000, samples:[
 {time:0,value:80,source:'sensorhub'},
 {time:60000,value:79,source:'sensorhub'},
 {time:120000,value:null,source:'sensorhub'},
 {time:180000,value:78,source:'sensorhub'},
 {time:172800000,value:73,source:'sensorhub'}
]};
renderBatteryHistory(battery);
`, context);
const capacityPath = elements.get('batteryCapacityPath').attrs.d;
assert.match(capacityPath, /^M 8.00 32.80 L 8.13 34.04 M /);
assert.match(capacityPath, /M 392.00 41.48/);
assert.equal(elements.get('batteryCapacityRange').textContent, 'Scale: 0 to 100 %');
vm.runInContext('renderBatteryHistory(battery)', context);
assert.equal(elements.get('batteryCapacityPath').attrs.d, capacityPath);
console.log('48-hour capacity chart checks passed');
