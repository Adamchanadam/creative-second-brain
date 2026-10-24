    const VS=`attribute vec3 p;attribute vec3 c;attribute float s;uniform mat4 m;varying vec3 v;varying float f;void main(){gl_Position=m*vec4(p,1.);gl_PointSize=s/max(1.,gl_Position.w);v=c;f=clamp(1.-(gl_Position.w-4.)/19.,.05,1.);}`;
    const FS=`precision mediump float;varying vec3 v;varying float f;uniform float g;void main(){vec2 q=gl_PointCoord*2.-1.;float d=dot(q,q);if(d>1.)discard;float core=exp(-d*13.);float halo=exp(-d*3.);gl_FragColor=vec4(v*(core+halo*g), (core*.92+halo*.18*g)*f);}`;
    const GFS=`precision mediump float;varying vec3 v;varying float f;void main(){vec2 q=gl_PointCoord*2.-1.;float d=dot(q,q);if(d>1.)discard;float g=exp(-d*3.2)*(1.-d);gl_FragColor=vec4(v,g*f);}`;
    const LVS=`attribute vec3 p;attribute vec4 c;uniform mat4 m;varying vec4 v;void main(){gl_Position=m*vec4(p,1.);v=c;}`;
    const LFS=`precision mediump float;varying vec4 v;void main(){gl_FragColor=v;}`;
    const QVS=`attribute vec2 p;varying vec2 u;void main(){u=p*.5+.5;gl_Position=vec4(p,0.,1.);}`;
    const NFS=`precision mediump float;
varying vec2 u;
uniform vec2 r;
uniform vec2 par;
uniform float t;
uniform float depth;
uniform float attention;
uniform float ambient;
uniform float seedOffset;
uniform float approach;
uniform float energy;
uniform vec3 tint;
uniform vec3 palette_a,palette_b,palette_c,palette_veil,palette_base;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1.,0.)),f.x),mix(h(i+vec2(0.,1.)),h(i+vec2(1.,1.)),f.x),f.y);}
float fbm(vec2 p){float v=0.,a=.53;mat2 turn=mat2(.82,-.57,.57,.82);for(int i=0;i<6;i++){v+=a*noise(p);p=turn*p*2.04+vec2(3.7,8.2);a*=.49;}return v;}
void main(){
 vec2 uv=(u-.5)*vec2(r.x/r.y,1.);
 vec2 origin=vec2(seedOffset,seedOffset*.713);
 vec2 p=uv*(1.62+depth*.24-approach*.18)*(1.-ambient*.26)+par;
 vec2 drift=vec2(t*.012,-t*.008);
 vec2 warp=vec2(fbm(p*1.4+drift+origin),fbm(p*1.7-drift+5.2+origin));
 float cloud=fbm(p*2.4+warp*1.55+drift+origin);
 float wisps=fbm(p*6.2+warp*2.1-drift*.5+origin);
 float ridge=pow(1.-abs(wisps*2.-1.),7.);
 float ribbon=p.y+.21*sin(p.x*2.8+warp.x*3.6)-p.x*.22;
 float envelope=exp(-ribbon*ribbon*3.6);
 float density=smoothstep(.37,.80,cloud)*envelope;
 float pockets=smoothstep(.31,.63,fbm(p*2.9-warp*.7));
 float filament=smoothstep(.50,.86,wisps)*density;
 vec3 cyan=palette_a,violet=palette_b,magenta=palette_c;
 float hue=fbm(p*.75+vec2(8.2,4.1)+origin);
 vec3 color=mix(cyan,violet,smoothstep(.28,.60,hue));
 color=mix(color,magenta,smoothstep(.57,.76,fbm(p*1.1+12.))*.7);
 color=mix(color,mix(tint,vec3(.3,.55,.9),.4),attention*.28);
 vec3 fog=color*density*(.36+pockets*1.4);
 fog+=mix(color,vec3(.62,.76,1.),.16)*filament*.8;
 fog+=color*ridge*density*.14;
 float cavity=smoothstep(.20,.55,fbm(p*1.8+warp+2.1));
 fog*=.23+.77*cavity;
 // Distant veil moves more slowly than the near ribbons.
 float veil=fbm(uv*1.8+par*.27-drift*.23+origin);
 fog+=palette_veil*smoothstep(.36,.73,veil)*.40;
 // Near translucent dust separates from the slower distant veil as the camera approaches.
 vec2 nearP=uv*(3.1-approach*1.05)+par*1.75+drift*.65;
 float nearDust=fbm(nearP+warp*.8+origin);
 float dustLane=smoothstep(.47,.73,nearDust)*envelope;
 float embers=pow(smoothstep(.44,.78,wisps),2.)*density;
 fog*=1.-dustLane*approach*.42;
 fog+=mix(color,vec3(.42,.72,1.),.20)*dustLane*approach*.30;
 fog+=mix(color,vec3(.78,.87,1.),.18)*embers*(approach*.95+energy*.8);
 // Quiet left margin for navigation; focus still changes the light behind the graph.
 float margin=mix(mix(.36,1.,smoothstep(.03,.40,u.x)),1.,ambient);
 float vign=1.-smoothstep(.42,1.30,length(uv));
 vec3 base=palette_base;
 gl_FragColor=vec4(base+fog*margin*vign*(1.12+attention*.10+ambient*.68),1.);
}
`;
    const BFS=`precision mediump float;varying vec2 u;uniform sampler2D x;uniform float cut;void main(){vec3 c=texture2D(x,u).rgb;float l=max(c.r,max(c.g,c.b));gl_FragColor=vec4(c*max(0.,l-cut)/(1.-cut),1.);}`;
    const UFS=`precision mediump float;varying vec2 u;uniform sampler2D x;uniform vec2 d;void main(){vec3 c=texture2D(x,u).rgb*.227027;c+=(texture2D(x,u+d).rgb+texture2D(x,u-d).rgb)*.1945946;c+=(texture2D(x,u+d*2.).rgb+texture2D(x,u-d*2.).rgb)*.1216216;c+=(texture2D(x,u+d*3.).rgb+texture2D(x,u-d*3.).rgb)*.054054;c+=(texture2D(x,u+d*4.).rgb+texture2D(x,u-d*4.).rgb)*.016216;gl_FragColor=vec4(c,1.);}`;
    const CFS=`precision mediump float;varying vec2 u;uniform sampler2D scene;uniform sampler2D bloom;uniform float bloomStrength;void main(){vec2 q=u-.5;float vign=1.-smoothstep(.24,.94,length(q));vec3 s=texture2D(scene,u).rgb;vec3 b=texture2D(bloom,u).rgb;vec3 c=s+b*bloomStrength;c=1.-exp(-c*1.42);c*=.72+.28*vign;gl_FragColor=vec4(c,1.);}`;

