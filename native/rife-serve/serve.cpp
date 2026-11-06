// SPDX-License-Identifier: Apache-2.0
// rife-ncnn-vulkan-serve: rife-ncnn-vulkan 20221029's own RIFE class (rife.cpp, warp.cpp and their shaders, unchanged),
// opened once and kept, making frames between two images for as long as its caller sends them (sprite-gen's resident
// RIFE, docs/loop-repair.md section 1). It replaces only the binary's main.cpp: where the binary decodes two files,
// makes one frame and exits, this reads raw pixels from stdin and writes the frame to stdout, again and again.
//
// The frame is the binary's own: the same RIFE::process, handed the same pixels (RGB, or BGR on Windows, as the
// binary's decoders hand it them), the same timestep (the caller sends the float the binary's atof gives) and the same
// augmentation. Built from the same source tree and ncnn commit, it compiles the same shaders.
//
// One process makes frames with one augmentation, named as the binary names it (-x, -z): RIFE::load keeps the shaders
// it compiles in statics shared by every RIFE of a process, so a second RIFE of another augmentation would run the
// first one's shaders. The binary never holds two; neither does this.
//
// Protocol, little-endian, on stdin and stdout (stderr is ncnn's and this file's log):
//   ready  (once, from here, the model loaded): "SGRS", u32 version, u32 n, n bytes: the GPU's name ("cpu" for -g -1)
//   request (from the caller): "SGRQ", u32 w, u32 h, f32 timestep, u32 flags (1: -x, 2: -z; this process's own),
//                              then w*h*3 bytes of the first image and w*h*3 bytes of the second, RGB, row by row
//   answer (from here):        "SGRA", i32 status, u32 n, n bytes: status 0, the frame (w*h*3 bytes, RGB);
//                              anything else, a message in UTF-8 — the request is refused, the process goes on
// It ends with exit 0 when stdin ends between requests; a request cut short is exit 2.
//
// usage: rife-ncnn-vulkan-serve -m <model dir> [-g <gpu id>] [-x] [-z]

#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#if _WIN32
#include <fcntl.h>
#include <io.h>
#include <wchar.h>
#endif

#include "gpu.h"
#include "rife.h"
#include "filesystem_utils.h"

static const uint32_t VERSION = 1;
static const uint32_t MAX_SIDE = 16384;
static const char* USAGE = "usage: rife-ncnn-vulkan-serve -m <model dir> [-g <gpu id>] [-x] [-z]\n";

static bool read_all(void* buf, size_t n)
{
    unsigned char* p = (unsigned char*)buf;
    while (n > 0)
    {
        size_t got = fread(p, 1, n, stdin);
        if (got == 0)
            return false;
        p += got;
        n -= got;
    }
    return true;
}

static bool write_all(const void* buf, size_t n)
{
    return fwrite(buf, 1, n, stdout) == n;
}

static uint32_t u32_at(const unsigned char* p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put_u32(std::vector<unsigned char>& out, uint32_t v)
{
    for (int i = 0; i < 4; i++)
        out.push_back((unsigned char)(v >> (8 * i)));
}

static bool answer(int32_t status, const void* data, uint32_t n)
{
    std::vector<unsigned char> head(4);
    memcpy(head.data(), "SGRA", 4);
    put_u32(head, (uint32_t)status);
    put_u32(head, n);
    bool ok = write_all(head.data(), head.size()) && (n == 0 || write_all(data, n));
    return fflush(stdout) == 0 && ok;
}

static bool refuse(const std::string& message)
{
    return answer(1, message.data(), (uint32_t)message.size());
}

#if _WIN32
// The binary decodes with WIC into BGR and RIFE was compiled to read BGR there (rife.cpp: bgr = 1 on _WIN32): the same
// pixels reach it here, turned to BGR and back.
static void swap_rb(unsigned char* p, size_t pixels)
{
    for (size_t i = 0; i < pixels; i++)
    {
        unsigned char r = p[3 * i];
        p[3 * i] = p[3 * i + 2];
        p[3 * i + 2] = r;
    }
}
#endif

#if _WIN32
int wmain(int argc, wchar_t** argv)
#else
int main(int argc, char** argv)
#endif
{
#if _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif

    path_t model;
    int gpuid = -2;
    uint32_t flags = 0;
    for (int i = 1; i < argc; i++)
    {
        path_t arg = argv[i];
        if (arg == PATHSTR("-m") && i + 1 < argc)
            model = argv[++i];
        else if (arg == PATHSTR("-g") && i + 1 < argc)
        {
#if _WIN32
            gpuid = _wtoi(argv[++i]);
#else
            gpuid = atoi(argv[++i]);
#endif
        }
        else if (arg == PATHSTR("-x"))
            flags |= 1;
        else if (arg == PATHSTR("-z"))
            flags |= 2;
        else
        {
            fprintf(stderr, "%s", USAGE);
            return 1;
        }
    }
    if (model.empty())
    {
        fprintf(stderr, "%s", USAGE);
        return 1;
    }

    // the model's kind, read from its name as main.cpp reads it
    bool rife_v2 = false;
    bool rife_v4 = false;
    if (model.find(PATHSTR("rife-v2")) != path_t::npos || model.find(PATHSTR("rife-v3")) != path_t::npos)
        rife_v2 = true;
    else if (model.find(PATHSTR("rife-v4")) != path_t::npos)
        rife_v4 = true;
    else if (model.find(PATHSTR("rife")) == path_t::npos)
    {
        fprintf(stderr, "unknown model dir type\n");
        return 1;
    }
    path_t modeldir = sanitize_dirpath(model);

    ncnn::create_gpu_instance();
    if (gpuid == -2)
        gpuid = ncnn::get_default_gpu_index();
    if (gpuid < -1 || gpuid >= ncnn::get_gpu_count())
    {
        fprintf(stderr, "invalid gpu device\n");
        ncnn::destroy_gpu_instance();
        return 1;
    }
    std::string name = gpuid == -1 ? std::string("cpu") : std::string(ncnn::get_gpu_info(gpuid).device_name());

    int code = 0;
    {
        // as main.cpp makes it for one GPU (its proc threads share the one RIFE)
        RIFE rife(gpuid, (flags & 1) != 0, (flags & 2) != 0, false, 1, rife_v2, rife_v4);
        rife.load(modeldir);

        std::vector<unsigned char> ready(4);
        memcpy(ready.data(), "SGRS", 4);
        put_u32(ready, VERSION);
        put_u32(ready, (uint32_t)name.size());
        ready.insert(ready.end(), name.begin(), name.end());
        if (!write_all(ready.data(), ready.size()) || fflush(stdout) != 0)
            code = 2;

        std::vector<unsigned char> in0data;
        std::vector<unsigned char> in1data;
        std::vector<unsigned char> outdata;
        while (code == 0)
        {
            unsigned char head[20];
            size_t got = fread(head, 1, sizeof(head), stdin);
            if (got == 0 && feof(stdin))
                break;  // the caller is done
            if (got != sizeof(head) && !read_all(head + got, sizeof(head) - got))
            {
                fprintf(stderr, "rife-ncnn-vulkan-serve: a request cut short\n");
                code = 2;
                break;
            }
            if (memcmp(head, "SGRQ", 4) != 0)
            {
                fprintf(stderr, "rife-ncnn-vulkan-serve: not a request\n");
                code = 2;
                break;
            }
            const uint32_t w = u32_at(head + 4);
            const uint32_t h = u32_at(head + 8);
            float timestep;
            memcpy(&timestep, head + 12, 4);
            const uint32_t asked = u32_at(head + 16);
            if (w == 0 || h == 0 || w > MAX_SIDE || h > MAX_SIDE)
            {
                fprintf(stderr, "rife-ncnn-vulkan-serve: a request of %u x %u\n", w, h);
                code = 2;
                break;
            }
            const size_t bytes = (size_t)w * h * 3;
            in0data.resize(bytes);
            in1data.resize(bytes);
            if (!read_all(in0data.data(), bytes) || !read_all(in1data.data(), bytes))
            {
                fprintf(stderr, "rife-ncnn-vulkan-serve: a request cut short\n");
                code = 2;
                break;
            }

            // what main.cpp refuses, refused in its words
            if (asked != flags)
            {
                if (!refuse("this process makes frames with augmentation flags " + std::to_string(flags) + ", not " + std::to_string(asked)))
                    code = 2;
                continue;
            }
            if (!(timestep > 0.f && timestep < 1.f))
            {
                if (!refuse("invalid timestep argument, must be 0~1"))
                    code = 2;
                continue;
            }
            if (!rife_v4 && timestep != 0.5f)
            {
                if (!refuse("only rife-v4 model support custom numframe and timestep"))
                    code = 2;
                continue;
            }

#if _WIN32
            swap_rb(in0data.data(), (size_t)w * h);
            swap_rb(in1data.data(), (size_t)w * h);
#endif
            ncnn::Mat in0(w, h, (void*)in0data.data(), (size_t)3, 3);
            ncnn::Mat in1(w, h, (void*)in1data.data(), (size_t)3, 3);
            outdata.assign(bytes, 0);
            ncnn::Mat out(w, h, (void*)outdata.data(), (size_t)3, 3);
            int ret = rife.process(in0, in1, timestep, out);
            if (ret != 0)
            {
                if (!refuse("RIFE::process failed (" + std::to_string(ret) + ")"))
                    code = 2;
                continue;
            }
#if _WIN32
            swap_rb(outdata.data(), (size_t)w * h);
#endif

            // What a frame left cached on the GPU is given back before the next, as the binary's exit gives it back:
            // between frames this holds the model and its pipelines only.
            if (gpuid != -1)
            {
                ncnn::VulkanDevice* vkdev = ncnn::get_gpu_device(gpuid);
                ncnn::VkAllocator* blob = vkdev->acquire_blob_allocator();
                blob->clear();
                vkdev->reclaim_blob_allocator(blob);
                ncnn::VkAllocator* staging = vkdev->acquire_staging_allocator();
                staging->clear();
                vkdev->reclaim_staging_allocator(staging);
            }

            if (!answer(0, outdata.data(), (uint32_t)bytes))
                code = 2;
        }
    }

    ncnn::destroy_gpu_instance();
    return code;
}
