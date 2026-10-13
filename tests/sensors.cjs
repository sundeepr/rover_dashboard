const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {textContent:'', classList:{toggle(){}},
    setAttribute(k,v) {this[k]=v;}, getAttribute(k) {return this[k];}, removeAttribute(k) {delete this[k];}});
  return elements.get(id);
}};
let now = 0;
const context = vm.createContext({document, URL, performance:{now: () => now}, window:{location:{origin:'http://rover'}}});
vm.runInContext(fs.readFileSync('app.js','utf8').split('// Dashboard startup')[0], context);
vm.runInContext(`
const grids = {}, sources = {};
renderReadingGrid = (id, items) => {grids[id] = Object.fromEntries(items);};
markSection = (id, source) => {sources[id] = source;};
renderImu({available:true, source:'sensorhub', updatedAt:'now', data:{
  euler_deg:{roll:-2,pitch:3,yaw:90}, heading_deg:90, compass:'E',
  accel_mps2:{x:0,y:0,z:9.81}, gyro_rads:{x:0,y:0,z:0.2}, mag_accuracy:0
}});
`, context);
assert.equal(vm.runInContext('grids.imuGrid.Roll',context), '-2.00°');
assert.equal(vm.runInContext('grids.imuGrid["Magnetometer accuracy"]',context), 'Unreliable');
assert.match(vm.runInContext('grids.imuGrid["Acceleration (m/s²)"]',context), /Z 9.81/);
vm.runInContext(`renderImu({available:false, reason:'Missing or stale telemetry'});`, context);
assert.equal(vm.runInContext('grids.imuGrid.Roll',context), '--');
assert.equal(vm.runInContext('sources.imuGrid',context), 'unavailable');
assert.match(elements.get('imuMessage').textContent, /stale/);
vm.runInContext(`renderGps({available:true,receiving:true,source:'sensorhub',latitude:12.9,longitude:77.6,speed:1.2,course:45});`, context);
assert.match(elements.get('gpsMapFrame').src, /12.900000/);
assert.equal(vm.runInContext('grids.gpsGrid.Speed',context), '1.20 m/s');
vm.runInContext(`renderGps({available:false,receiving:true,reason:'Waiting for GPS fix',satellites:0,satellitesInView:8});`, context);
assert.match(elements.get('gpsMapFrame').src, /ll=0,0/);
assert.equal(vm.runInContext('grids.gpsGrid["Satellites used / in view"]',context), '0 / 8');
assert.equal(vm.runInContext('grids.gpsGrid.Latitude',context), '--');
console.log('GPS and IMU rendering checks passed');

// Sub-meter jitter updates numbers/link, but never reloads the iframe.
const map = elements.get('gpsMapFrame');
let reloads = 0, mapSrc = map.src;
Object.defineProperty(map, 'src', {get: () => mapSrc, set: value => {reloads++; mapSrc = value;}});
const gps = (latitude, longitude = 77.6) => vm.runInContext(`renderGps({available:true,receiving:true,source:'sensorhub',latitude:${latitude},longitude:${longitude}})`,context);
gps(12.9);
assert.equal(reloads,1);
now = 20000;
gps(12.900001);
assert.equal(reloads,1);
assert.equal(vm.runInContext('grids.gpsGrid.Latitude',context),'12.900001°');
assert.match(elements.get('gpsMapLink').href,/12.900001/);
gps(12.90005); // about 5.5 m from displayed location
assert.equal(reloads,1);
gps(12.9001); // accumulated movement exceeds 10 m
assert.equal(reloads,2);
now = 21000;
gps(12.9003); // movement is rate-limited
assert.equal(reloads,2);
now = 35000;
gps(12.9003);
assert.equal(reloads,3);
vm.runInContext('renderGps(undefined)',context);
assert.equal(reloads,4);
assert.match(map.src,/ll=0,0/);
gps(12.9003); // recovery renders immediately, without waiting 15 seconds
assert.equal(reloads,5);
assert.ok(vm.runInContext('gpsDistanceMeters({latitude:0,longitude:179.999999},{latitude:0,longitude:-179.999999})',context) < 1);
console.log('GPS map jitter, cumulative movement, rate limit and recovery checks passed');

vm.runInContext(`renderController({available:true,source:'sensorhub',updatedAt:'now',data:{
  board_t_ms:1039228,armed:true,autonomy:false,host_stale:true,
  rc:{throttle:0,steer:0},host:{throttle:null,radius_mm:null,aux:null},
  wheel_base_mm:650,cmd:{left:0,right:0},pump_cmd:1,extra:{},wheel_base_matches:true
}})`,context);
assert.equal(vm.runInContext('grids.controllerGrid.Arming',context),'Armed');
assert.equal(vm.runInContext('grids.controllerGrid.Autonomy',context),'Disabled');
assert.equal(vm.runInContext('grids.controllerGrid["Left motor command"]',context),'0');
assert.equal(vm.runInContext('grids.controllerGrid["Host throttle"]',context),'--');
assert.equal(vm.runInContext('grids.controllerGrid["Pump command"]',context),'1');
assert.match(elements.get('controllerWarning').textContent,/stale host commands/);
vm.runInContext(`renderController({available:true,source:'sensorhub',data:{armed:false,host_stale:false,wheel_base_matches:false}})`,context);
assert.equal(vm.runInContext('grids.controllerGrid.Arming',context),'Disarmed');
assert.equal(vm.runInContext('grids.controllerGrid["Host commands"]',context),'Fresh');
assert.match(elements.get('controllerWarning').textContent,/wheel base differs/);
vm.runInContext(`renderController({available:false,reason:'Missing or stale telemetry'})`,context);
assert.equal(vm.runInContext('grids.controllerGrid.Arming',context),'--');
assert.equal(vm.runInContext('sources.controllerGrid',context),'unavailable');
assert.equal(elements.get('controllerWarning').textContent,'');
assert.match(elements.get('controllerMessage').textContent,/stale telemetry/);
console.log('STM32 controller rendering checks passed');

vm.runInContext('renderControllerJoystick({throttle:0,steer:0})',context);
assert.equal(elements.get('controllerJoystickDot').cx,140);
assert.equal(elements.get('controllerJoystickDot').cy,140);
assert.equal(elements.get('controllerJoystickPosition').visibility,'visible');
vm.runInContext('renderControllerJoystick({throttle:100,steer:-100})',context);
assert.equal(elements.get('controllerJoystickDot').cx,60);
assert.equal(elements.get('controllerJoystickDot').cy,60);
vm.runInContext('renderControllerJoystick({throttle:-100,steer:100})',context);
assert.equal(elements.get('controllerJoystickDot').cx,220);
assert.equal(elements.get('controllerJoystickDot').cy,220);
vm.runInContext('renderController({available:false})',context);
assert.equal(elements.get('controllerJoystickPosition').visibility,'hidden');
assert.match(elements.get('controllerJoystickReading').textContent,/unavailable/);
vm.runInContext('renderControllerJoystick({throttle:null,steer:0})',context);
assert.equal(elements.get('controllerJoystickPosition').visibility,'hidden');
vm.runInContext('renderControllerJoystick({throttle:101,steer:0})',context);
assert.equal(elements.get('controllerJoystickPosition').visibility,'hidden');
assert.match(elements.get('controllerJoystickReading').textContent,/Outside/);
console.log('RC joystick neutral, direction, missing and out-of-range checks passed');
