#include <complex.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include <liquid/liquid.h>

typedef struct {
    float sample_rate;
    float channel_rate;
    unsigned int decim;
    nco_crcf nco;
    firdecim_crcf decim_filter;
    liquid_float_complex *work;
    liquid_float_complex *mixed;
    unsigned int work_cap;
    unsigned int mixed_cap;
    liquid_float_complex *remainder;
    unsigned int remainder_len;
    unsigned int remainder_cap;
} sdrshark_fm_channelizer;

static unsigned int clamp_decim(float sample_rate, float channel_rate) {
    if (sample_rate <= 0.0f || channel_rate <= 0.0f) return 1;
    unsigned int decim = (unsigned int)lrintf(sample_rate / channel_rate);
    if (decim < 1) decim = 1;
    if (decim > 4096) decim = 4096;
    return decim;
}

void *sdrshark_fm_channelizer_create(float sample_rate, float offset_hz, float channel_rate) {
    sdrshark_fm_channelizer *q = (sdrshark_fm_channelizer *)calloc(1, sizeof(sdrshark_fm_channelizer));
    if (!q) return NULL;

    q->sample_rate = sample_rate;
    q->decim = clamp_decim(sample_rate, channel_rate);
    q->channel_rate = sample_rate / (float)q->decim;
    q->nco = nco_crcf_create(LIQUID_NCO);
    q->decim_filter = NULL;
    q->remainder_cap = q->decim > 1 ? q->decim - 1 : 1;
    q->remainder = (liquid_float_complex *)calloc(q->remainder_cap, sizeof(liquid_float_complex));
    if (!q->nco || !q->remainder) {
        if (q->nco) nco_crcf_destroy(q->nco);
        free(q->remainder);
        free(q);
        return NULL;
    }

    nco_crcf_set_frequency(q->nco, 2.0f * (float)M_PI * offset_hz / sample_rate);
    if (q->decim > 1) {
        q->decim_filter = firdecim_crcf_create_kaiser(q->decim, 8, 70.0f);
        if (!q->decim_filter) {
            nco_crcf_destroy(q->nco);
            free(q->remainder);
            free(q);
            return NULL;
        }
    }
    return (void *)q;
}

void sdrshark_fm_channelizer_destroy(void *handle) {
    sdrshark_fm_channelizer *q = (sdrshark_fm_channelizer *)handle;
    if (!q) return;
    if (q->decim_filter) firdecim_crcf_destroy(q->decim_filter);
    if (q->nco) nco_crcf_destroy(q->nco);
    free(q->work);
    free(q->mixed);
    free(q->remainder);
    free(q);
}

float sdrshark_fm_channelizer_output_rate(void *handle) {
    sdrshark_fm_channelizer *q = (sdrshark_fm_channelizer *)handle;
    return q ? q->channel_rate : 0.0f;
}

static int ensure_capacity(liquid_float_complex **buf, unsigned int *cap, unsigned int need) {
    if (*cap >= need) return 0;
    liquid_float_complex *next = (liquid_float_complex *)realloc(*buf, need * sizeof(liquid_float_complex));
    if (!next) return -1;
    *buf = next;
    *cap = need;
    return 0;
}

int sdrshark_fm_channelizer_process(
    void *handle,
    const liquid_float_complex *input,
    unsigned int input_len,
    liquid_float_complex *output,
    unsigned int output_cap
) {
    sdrshark_fm_channelizer *q = (sdrshark_fm_channelizer *)handle;
    if (!q || !input || !output || input_len == 0) return 0;

    unsigned int total = q->remainder_len + input_len;
    if (ensure_capacity(&q->work, &q->work_cap, total) != 0) return -1;
    if (q->remainder_len > 0) {
        memcpy(q->work, q->remainder, q->remainder_len * sizeof(liquid_float_complex));
    }
    memcpy(q->work + q->remainder_len, input, input_len * sizeof(liquid_float_complex));

    if (q->decim <= 1) {
        if (output_cap < total) return -2;
        nco_crcf_mix_block_down(q->nco, q->work, output, total);
        q->remainder_len = 0;
        return (int)total;
    }

    unsigned int out_len = total / q->decim;
    unsigned int usable = out_len * q->decim;
    if (out_len == 0) {
        if (total > q->remainder_cap) return -3;
        memcpy(q->remainder, q->work, total * sizeof(liquid_float_complex));
        q->remainder_len = total;
        return 0;
    }
    if (output_cap < out_len) return -2;
    if (ensure_capacity(&q->mixed, &q->mixed_cap, usable) != 0) return -1;

    nco_crcf_mix_block_down(q->nco, q->work, q->mixed, usable);
    firdecim_crcf_execute_block(q->decim_filter, q->mixed, out_len, output);

    q->remainder_len = total - usable;
    if (q->remainder_len > 0) {
        memcpy(q->remainder, q->work + usable, q->remainder_len * sizeof(liquid_float_complex));
    }
    return (int)out_len;
}
