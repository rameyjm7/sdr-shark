import sys
import types


def test_soapy_backend_lists_and_selects_devices(monkeypatch):
    class FakeDevice:
        @staticmethod
        def enumerate(args):
            if args.get("driver") != "rtlsdr":
                return []
            return [{"driver": "rtlsdr", "serial": "abc123", "label": "RTL Test"}]

    fake_soapy = types.SimpleNamespace(
        Device=FakeDevice,
        SOAPY_SDR_RX=1,
        SOAPY_SDR_CS16=2,
        SOAPY_SDR_CF32=3,
    )

    monkeypatch.setitem(sys.modules, "SoapySDR", fake_soapy)
    monkeypatch.setenv("SDR_BACKEND", "soapy")
    monkeypatch.setenv("SDR_SOAPY_DRIVERS", "rtlsdr")

    from sdr_plot_backend.sdr_generic import SDRGeneric

    sdr = SDRGeneric("rtlsdr", center_freq=100e6, sample_rate=2.4e6, bandwidth=2.4e6, gain=20, size=1024)

    devices = sdr.list_devices()
    assert devices[0]["id"] == "rtlsdr:0"
    assert devices[0]["backend"] == "soapy"
    assert sdr.select_device("rtlsdr:0") is True
    assert sdr.device_id == "rtlsdr:0"
