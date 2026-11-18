#include <stdint.h>
#include <stddef.h>

/* Reduce a flat int16 array into per-group (min, max) pairs.
 * The last group may be partial. Returns the number of groups written.
 * Works both for raw samples (group = factor) and for interleaved
 * (min,max) bin pairs of a lower level (group = 2 * factor).
 */
size_t minmax_reduce(const int16_t *src, size_t n, size_t group,
                     int16_t *dst_min, int16_t *dst_max) {
    size_t n_groups = 0;
    size_t i = 0;
    while (i < n) {
        size_t end = i + group;
        if (end > n) end = n;
        int16_t mn = src[i], mx = src[i];
        for (size_t j = i + 1; j < end; j++) {
            int16_t v = src[j];
            if (v < mn) mn = v;
            if (v > mx) mx = v;
        }
        dst_min[n_groups] = mn;
        dst_max[n_groups] = mx;
        n_groups++;
        i = end;
    }
    return n_groups;
}
