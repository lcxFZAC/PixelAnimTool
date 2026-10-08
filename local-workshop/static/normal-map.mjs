// silhouette-bevel-v1. Linear tangent data: +X right, +Y up, +Z viewer.
export function buildNormalMap(rgba,w,h,strength=65){
  const d=new Float64Array(w*h),diag=Math.sqrt(2);
  for(let i=0;i<d.length;i++)d[i]=rgba[i*4+3]?w+h:0;
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){const i=y*w+x;if(!rgba[i*4+3])continue;d[i]=Math.min(d[i],x?d[i-1]+1:1,y?d[i-w]+1:1,x&&y?d[i-w-1]+diag:diag,y&&x+1<w?d[i-w+1]+diag:diag);}
  for(let y=h-1;y>=0;y--)for(let x=w-1;x>=0;x--){const i=y*w+x;if(!rgba[i*4+3])continue;d[i]=Math.min(d[i],x+1<w?d[i+1]+1:1,y+1<h?d[i+w]+1:1,x+1<w&&y+1<h?d[i+w+1]+diag:diag,x&&y+1<h?d[i+w-1]+diag:diag);}
  const radius=Math.max(3,Math.min(12,Math.min(w,h)/8)),depth=radius*.8*strength/100;
  const heights=d.map(v=>depth*Math.sin(Math.min(v/radius,1)*Math.PI/2)),out=new Uint8ClampedArray(w*h*4);
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){const i=y*w+x,a=rgba[i*4+3];if(!a){out.set([128,128,255,0],i*4);continue;}const dx=((x+1<w?heights[i+1]:0)-(x?heights[i-1]:0))/2,dy=((y+1<h?heights[i+w]:0)-(y?heights[i-w]:0))/2,length=Math.sqrt(dx*dx+dy*dy+1);out.set([-dx,dy,1].map(v=>Math.floor((v/length*.5+.5)*255+.5)).concat(a),i*4);}
  return out;
}
