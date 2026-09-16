import { defineConfig } from 'vite';
export default defineConfig({root:'../pivot/web',server:{host:'127.0.0.1',port:5173,strictPort:true},build:{outDir:'../../dashboard/dist',emptyOutDir:true}});
