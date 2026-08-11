const defaultApiBaseUrl =
  window.location.port === '3000'
    ? `http://${window.location.hostname}:5000`
    : window.location.origin;

const config = {
  base_url: process.env.REACT_APP_API_BASE_URL || defaultApiBaseUrl
};

export default config;
