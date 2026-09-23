const http = require('node:http');
const fs = require('node:fs');
const orig = http.Server.prototype.emit;
http.Server.prototype.emit = function(event, req, res, ...rest) {
  if (event === 'request') {
    const begin = process.cpuUsage();
    const time = performance.now();
    const path = req.url;
    res.once('finish', () => {
      const cpu = process.cpuUsage(begin);
      fs.appendFileSync(process.env.AUDIT_CPU_LOG, JSON.stringify({path, status:res.statusCode, cpu_ms:(cpu.user+cpu.system)/1000, wall_ms:performance.now()-time})+'\n');
    });
  }
  return orig.call(this, event, req, res, ...rest);
};
