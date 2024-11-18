const os = require('os');
const fs = require('fs');
const path = require('path');

// Function to get the IPv4 address
function getLocalIPAddress() {
  const interfaces = os.networkInterfaces();
  for (const interfaceName in interfaces) {
    const iface = interfaces[interfaceName];
    for (let i = 0; i < iface.length; i++) {
      const alias = iface[i];
      if (alias.family === 'IPv4' && !alias.internal && alias.address !== '127.0.0.1') {
        return alias.address;
      }
    }
  }
  return 'localhost';
}

// Determine the IP address
const ipAddress = "10.139.1.86"; // getLocalIPAddress();

// Define the proxy URL based on the IP address
const proxyUrl = `http://${ipAddress}:5000`;

// Path to package.json
const packageJsonPath = path.resolve(__dirname, 'package.json');

// Read package.json
const packageJson = require(packageJsonPath);

// Set the proxy field in package.json
packageJson.proxy = proxyUrl;

// Write the updated package.json back to the file system
fs.writeFileSync(packageJsonPath, JSON.stringify(packageJson, null, 2));

console.log(`Proxy set to ${proxyUrl}`);
