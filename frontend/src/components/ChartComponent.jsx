import React, { useEffect, useState, useRef } from 'react';
import axios from 'axios';
import Plot from 'react-plotly.js';
import '../App.css';

const ChartComponent = ({ settings, sweepSettings, setSweepSettings, minY, maxY, updateInterval, waterfallSamples, showWaterfall, plotWidth, verticalLines, horizontalLines }) => {
  const [fftData, setFftData] = useState([]);
  const [fftMaxData, setFftMaxData] = useState([]);
  const [persistanceData, setPersistanceData] = useState([]);
  const [waterfallData, setWaterfallData] = useState([]);
  const [time, setTime] = useState('');
  const [peaks, setPeaks] = useState([]);
  const prevTickValsRef = useRef([]);
  const prevTickTextRef = useRef([]);
  const [currentFrequency, setCurrentFrequency] = useState(0);
  const [plotHeight, setPlotHeight] = useState(35); // Start with a default value

  useEffect(() => {
    const adjustPlotHeight = () => {
      const containerHeight = window.innerHeight;
      const availableHeight = containerHeight - 100; // Adjust based on the height of other elements (like the control panel)
      const calculatedHeight = (availableHeight * 0.4) / containerHeight * 100; // Set to 40% of the available height
      setPlotHeight(calculatedHeight);
    };

    adjustPlotHeight();
    window.addEventListener('resize', adjustPlotHeight);

    return () => {
      window.removeEventListener('resize', adjustPlotHeight);
    };
  }, []);

  useEffect(() => {
    console.log(`minY: ${minY}, maxY: ${maxY}`);
  }, [minY, maxY]);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await axios.get('/api/data');
        const data = response.data;
        // Replace NaN values in FFT data
        const sanitizedFftData = data.fft.map(value => isNaN(value) ? -255 : value);
        setFftData(sanitizedFftData);
        // Replace NaN values in Waterfall data
        const sanitizedWaterfallData = data.waterfall.map(row =>
          row.map(value => isNaN(value) ? -255 : value)
        );
        setWaterfallData(sanitizedWaterfallData.slice(-waterfallSamples));

        setTime(data.time);

        if (data.settings.sweeping_enabled) {
          setSweepSettings({
            frequency_start: data.settings.sweep_settings.frequency_start,
            frequency_stop: data.settings.sweep_settings.frequency_stop,
            sweeping_enabled: data.settings.sweeping_enabled,
            bandwidth: data.settings.sweep_settings.frequency_stop - data.settings.sweep_settings.frequency_start,
          });

          const currentFreq = data.settings.center_freq;
          setCurrentFrequency(currentFreq);
        } else {
          setCurrentFrequency(settings.frequency * 1e6);
        }
      } catch (error) {
        console.error('Error fetching data:', error);
      }
    };

    const interval = setInterval(fetchData, updateInterval);
    return () => clearInterval(interval);
  }, [updateInterval, waterfallSamples, setSweepSettings, settings.frequency, settings.sampleRate]);



  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await axios.get('/api/data_ext');
        const data = response.data;
        // Replace NaN values in FFT data
        const sanitizedMaxFftData = data.max.map(value => isNaN(value) ? -255 : value);
        setFftMaxData(sanitizedMaxFftData);
        const sanitizedPersistanceData = data.persistance.map(value => isNaN(value) ? -255 : value);
        setPersistanceData(data.persistance);
      } catch (error) {
        console.error('Error fetching data:', error);
      }
    };
    const interval = setInterval(fetchData, 500);
    return () => clearInterval(interval);
  }, [updateInterval, settings.frequency, settings.sampleRate]);

  useEffect(() => {
    const fetchPeaks = async () => {
      try {
        const response = await axios.get('/api/analytics', {
          params: {
            min_peak_distance: settings.minPeakDistance * 1e3, // Convert to Hz
            number_of_peaks: settings.numberOfPeaks,
          }
        });
        setPeaks(response.data.peaks);
      } catch (error) {
        console.error('Error fetching peaks:', error);
      }
    };

    const interval = setInterval(fetchPeaks, 500);
    return () => clearInterval(interval);
  }, [settings.minPeakDistance, settings.numberOfPeaks]);

  const generateColor = (value) => {
    if (value >= 0) {
      return 'rgb(0, 255, 0)';
    } else if (value >= -10) {
      const ratio = (value + 10) / 10;
      const red = Math.floor(255 * (1 - ratio));
      const green = 255;
      const blue = 0;
      return `rgb(${red}, ${green}, ${blue})`;
    } else if (value >= -20) {
      const ratio = (value + 20) / 10;
      const red = 255;
      const green = Math.floor(255 * ratio);
      const blue = 0;
      return `rgb(${red}, ${green}, ${blue})`;
    } else {
      return 'rgb(255, 0, 0)';
    }
  };

  const generateAnnotations = (peaks, baseFreq, freqStep) => {
    const startFreq = baseFreq;
    const endFreq = baseFreq + freqStep * (fftData.length - 1);
    if (!settings.peakDetection) return [];

    return peaks
      .filter((peak) => peak.frequency >= startFreq && peak.frequency <= endFreq)
      .map((peak) => {
        // Correct the frequency calculation here
        const freq = startFreq + ((peak.frequency - startFreq) / freqStep) * freqStep;
        const power = peak.power.toFixed(2);
        const powerColor = generateColor(power);
        return {
          x: freq.toFixed(2),
          y: parseFloat(power),
          xref: 'x',
          yref: 'y',
          text: `${(freq / 1e6).toFixed(2)} MHz<br><span style="color:${powerColor}">${power} dB</span>`,
          showarrow: true,
          arrowhead: 2,
          ax: 0,
          ay: -40,
          font: {
            size: 12,
            color: 'white',
          },
          align: 'center',
        };
      });
  };

  const baseFreq = sweepSettings.sweeping_enabled
    ? sweepSettings.frequency_start
    : (settings.frequency - settings.sampleRate / 2) * 1e6;
  const freqStep = (sweepSettings.sweeping_enabled ? sweepSettings.bandwidth : settings.sampleRate * 1e6) / fftData.length;
  const peakAnnotations = generateAnnotations(peaks, baseFreq, freqStep);

  const generateTickValsAndLabels = (startFreq, stopFreq) => {
    const numTicks = settings.numTicks || 5; // Default to 5 if not set
    const totalBandwidth = stopFreq - startFreq;
    const step = totalBandwidth / (numTicks - 1); // Adjust step calculation for numTicks

    const tickVals = [];
    const tickText = [];
    for (let i = 0; i < numTicks; i++) {
      const freq = startFreq + i * step;
      tickVals.push(freq);
      tickText.push((freq / 1e6).toFixed(2)); // Convert to MHz
    }
    tickVals.push(stopFreq * 0.999);
    tickText.push((stopFreq / 1e6).toFixed(2)); // Convert to MHz
    return { tickVals, tickText };
  };

  const { tickVals, tickText } = generateTickValsAndLabels(
    sweepSettings.sweeping_enabled ? sweepSettings.frequency_start : (settings.frequency - settings.sampleRate / 2) * 1e6,
    sweepSettings.sweeping_enabled ? sweepSettings.frequency_stop : (settings.frequency + settings.sampleRate / 2) * 1e6
  );

  // Ensure tick values are within a valid range
  const isValidTickVals = tickVals.every(val => val >= 1e6 && val <= 1e10); // Adjust range as necessary

  if (isValidTickVals) {
    if (
      JSON.stringify(tickVals) !== JSON.stringify(prevTickValsRef.current) ||
      JSON.stringify(tickText) !== JSON.stringify(prevTickTextRef.current)
    ) {
      prevTickValsRef.current = tickVals;
      prevTickTextRef.current = tickText;
    }
  } else {
    // Log an error message if the tick values are out of range
    console.error("Tick values out of range:", tickVals);
  }

  // Initialize verticalLineTraces before usage
  let verticalLineTraces = [];

  if (verticalLines && verticalLines.length > 0) {
    verticalLineTraces = verticalLines.map(({ frequency }) => {
      const lineColor = 'rgb(255, 0, 0)'; // Red color for vertical lines
      return {
        x: [frequency * 1e6, frequency * 1e6], // Fixed frequency for both x points
        y: [minY, maxY],           // Span the full y-axis range
        type: 'scatter',
        mode: 'lines',
        line: { color: lineColor, width: 2 },
        hoverinfo: 'x',             // Show frequency on hover
        name: `${frequency.toFixed(2)} MHz`, // Label for the legend
      };
    });
  }

  useEffect(() => {
    console.log(`Vertical lines to be added: ${JSON.stringify(verticalLines)}`);
  }, [verticalLines]);

  // Initialize horizontalLineTraces before usage
  let horizontalLineTraces = [];

  if (horizontalLines && horizontalLines.length > 0) {
    horizontalLineTraces = horizontalLines.map(({ power }) => {
      const lineColor = 'rgb(255, 0, 0)'; // Red color for horizontal lines
      return {
        x: [baseFreq, baseFreq + freqStep * (fftData.length - 1)], // Span the entire frequency range
        y: [power, power],           // Fixed power level for both y points
        type: 'scatter',
        mode: 'lines',
        line: { color: lineColor, width: 2 },
        hoverinfo: 'y',             // Show power on hover
        name: `${power.toFixed(2)} dB`, // Label for the legend
      };
    });
  }

  useEffect(() => {
    console.log(`Horizontal lines to be added: ${JSON.stringify(horizontalLines)}`);
  }, [horizontalLines]);

  // this is called when a selection is made, allowing us to get the coordinates and send it to the backend to extract that waterfall
  const handleRelayout = (eventData) => {
    if (eventData['xaxis.range[0]'] && eventData['xaxis.range[1]'] &&
      eventData['yaxis.range[0]'] && eventData['yaxis.range[1]']) {

      // Extract the box selection coordinates
      const xStart = eventData['xaxis.range[0]'];
      const xEnd = eventData['xaxis.range[1]'];
      const yStart = eventData['yaxis.range[0]'];
      const yEnd = eventData['yaxis.range[1]'];

      console.log(`Box selection coordinates: 
            xStart: ${xStart}, xEnd: ${xEnd}, 
            yStart: ${yStart}, yEnd: ${yEnd}`);
      console.log(settings);
      // Assuming frequency and sampleRate are part of your SDR settings
      const frequency = settings.frequency; // Adjust based on your actual settings object structure
      const sampleRate = settings.sampleRate; // Adjust based on your actual settings object structure

      // Prepare the coordinates data to be sent to the backend
      const coordinates = {
        xStart,
        xEnd,
        yStart,
        yEnd,
        filename: `${frequency}_${sampleRate}`, // Default filename
      };

      // Send the initial save request with the default filename
      fetch('/api/save_selection', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(coordinates),
      })
        .then(response => response.json())
        .then(data => {
          console.log('Initial save success:', data);

          // After the initial save, prompt the user to confirm or change the filename
          const userFilename = prompt('Enter filename (leave blank to keep default):', coordinates.filename);

          if (userFilename && userFilename !== coordinates.filename) {
            // Send the rename request if the filename is different
            const renameData = {
              old_filename: coordinates.filename,
              new_filename: userFilename,
            };

            fetch('/api/move', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
              },
              body: JSON.stringify(renameData),
            })
              .then(response => response.json())
              .then(renameData => {
                console.log('Rename success:', renameData);
              })
              .catch((error) => {
                console.error('Rename error:', error);
              });
          }
        })
        .catch((error) => {
          console.error('Initial save error:', error);
        });
    }
  };

  return (
    <div>
      <Plot
        data={[
          {
            x: Array.isArray(fftData) ? fftData.map((_, index) => {
              return (baseFreq + index * freqStep).toFixed(2);
            }) : [],
            y: Array.isArray(fftData) ? fftData : [],
            type: 'scatter',
            mode: 'lines',
            marker: { color: 'orange' },
            line: { shape: 'spline', width: 1 }, // Thinner trace lines
            showlegend: false, // Hide this trace from the legend
          },
          settings.showMaxTrace && {  // Conditionally add the Max FFT trace
            x: Array.isArray(fftMaxData) ? fftMaxData.map((_, index) => {
              return (baseFreq + index * freqStep).toFixed(2);
            }) : [],
            y: Array.isArray(fftMaxData) ? fftMaxData : [],
            type: 'scatter',
            mode: 'lines',
            marker: { color: 'green' },
            line: { shape: 'spline', width: 1 }, // Thinner trace lines
            showlegend: false, // Show this trace in the legend
            name: 'Max FFT Data', // Label for the legend
          },
          settings.showPersistanceTrace && {  // Conditionally add the Max FFT trace
            x: Array.isArray(persistanceData) ? persistanceData.map((_, index) => {
              return (baseFreq + index * freqStep).toFixed(2);
            }) : [],
            y: Array.isArray(persistanceData) ? persistanceData : [],
            type: 'scatter',
            mode: 'lines',
            marker: { color: 'blue' },
            line: { shape: 'spline', width: 1 }, // Thinner trace lines
            showlegend: false, // Show this trace in the legend
            name: 'Persistance FFT Data', // Label for the legend
          },
          ...verticalLineTraces.map(trace => ({
            ...trace,
            showlegend: false, // Hide vertical lines from the legend
          })),
          ...horizontalLineTraces.map(trace => ({
            ...trace,
            showlegend: false, // Hide horizontal lines from the legend
          })),
        ].filter(Boolean)} // Filter out false/null traces
        layout={{
          title: `Spectrum Viewer (Time: ${time}) (Freq: ${(currentFrequency / 1e6).toFixed(2)})`,
          xaxis: {
            title: 'Frequency (MHz)',
            color: 'white',
            gridcolor: '#444',
            tickvals: prevTickValsRef.current,
            ticktext: prevTickTextRef.current,
          },
          yaxis: {
            title: 'Amplitude (dB)',
            range: [minY, maxY],
            color: 'white',
            gridcolor: '#444',
            zeroline: false // Remove the white line across the 0 mark
          },
          margin: {
            l: 50,
            r: 50,
            b: 50,
            t: 50,
            pad: 4
          },
          autosize: true,  // Let Plotly auto size
          paper_bgcolor: '#000',
          plot_bgcolor: '#000',
          font: {
            color: 'white',
          },
          annotations: [...peakAnnotations].filter(Boolean),
        }}
        style={{ width: `${plotWidth}vw` }}
      />
      {showWaterfall && (
        <Plot
          data={[
            {
              z: waterfallData,
              type: 'heatmap',
              colorscale: 'Jet',
              zsmooth: 'fast',
              zmin: minY,
              zmax: maxY,
              showscale: false, // Remove the color scale
            },
          ]}
          layout={{
            title: '',
            xaxis: {
              title: 'Frequency (MHz)',
              color: 'white',
              gridcolor: '#444',
              zeroline: false, // Remove the white line across the 0 mark
              tickvals: prevTickValsRef.current,
              ticktext: prevTickTextRef.current,
            },
            yaxis: {
              title: 'Samples',
              color: 'white',
              gridcolor: '#444',
            },
            margin: {
              l: 50,
              r: 50,
              b: 50,
              t: 0,
              pad: 4
            },
            autosize: true,  // Let Plotly auto size
            paper_bgcolor: '#000',
            plot_bgcolor: '#000',
            font: {
              color: 'white',
            },
          }}
          config={{
            displayModeBar: false, // Hide the mode bar
          }}
          style={{ width: `${plotWidth}vw` }}
          onRelayout={handleRelayout} // Attach the relayout event handler
        />
      )}
    </div>
  );
};

export default ChartComponent;
