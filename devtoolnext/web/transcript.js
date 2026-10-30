/* Terminal projection only. Raw records stay authoritative on disk and in the database. */
(function(root){
  const MAX_TERM_LINES=2000;
  const MAX_TERM_COLS=4000;
  const bytesOf = record => Uint8Array.from(atob(record.data_base64), c => c.charCodeAt(0));
  const hexOf = bytes => [...bytes].map(b => b.toString(16).padStart(2, '0').toUpperCase()).join(' ');
  function applySgr(ansi, raw){
    const parts=raw.length?raw.split(';'):['0'];
    for(const part of parts){
      const n=Number(part||'0');
      if(n===0){ansi.fg='';ansi.bold=false}
      else if(n===1){ansi.bold=true}
      else if(n===22){ansi.bold=false}
      else if(n>=30&&n<=37){ansi.fg='c'+(n-30)}
      else if(n>=90&&n<=97){ansi.fg='c'+(n-82)}
      else if(n===39){ansi.fg=''}
    }
    ansi.cls=((ansi.fg?'ansi-'+ansi.fg:'')+(ansi.bold?' ansi-bold':'')).trim();
  }
  /* Terminal view: one merged stream, carriage returns end lines, no direction column. */
  function createTerminal(encoding, includeTx=true){
    const decoder=new TextDecoder(encoding);
    const ansi={mode:0, buf:'', fg:'', bold:false, cls:''};
    let seq=0;
    function makeLine(){
      return {id:++seq, text:'', styles:[], rev:0};
    }
    let line=makeLine();
    const lines=[line];
    let col=0;

    function writeChar(ch){
      if(col>MAX_TERM_COLS)return;
      line.text=line.text.slice(0,col)+ch+line.text.slice(col+1);
      line.styles[col]=ansi.cls;
      line.rev++;
      col++;
    }
    function nextLine(){
      line=makeLine();
      lines.push(line);
      if(lines.length>MAX_TERM_LINES)lines.splice(0, lines.length-MAX_TERM_LINES);
      col=0;
    }
    function consume(text){
      for(let i=0;i<text.length;i++){
        const code=text.charCodeAt(i);
        if(ansi.mode===1){
          if(code===91){ansi.mode=2;ansi.buf=''}
          else if(code===93){ansi.mode=3;ansi.buf=''}
          else ansi.mode=0;
          continue;
        }
        if(ansi.mode===2){
          if(code>=0x40&&code<=0x7e){
            if(text[i]==='m')applySgr(ansi, ansi.buf);
            ansi.mode=0;ansi.buf='';
          }else ansi.buf+=text[i];
          continue;
        }
        if(ansi.mode===3){
          if(code===7||(code===92&&ansi.buf.endsWith('\u001b')))ansi.mode=0;
          else ansi.buf=(ansi.buf+text[i]).slice(-4);
          continue;
        }
        if(code===27){ansi.mode=1;continue}
        // Terminal semantics: CR returns to column 0, only LF advances a line.
        // readline emits "\r\n" then "\r" to repaint the prompt; treating both as breaks adds a blank line.
        if(code===13){col=0;continue}
        if(code===10){nextLine();continue}
        if(code===8){col=Math.max(0,col-1);continue}
        if(code===7||code===0||code===12)continue;
        if(code<0x20&&code!==9)continue;
        writeChar(text[i]);
      }
    }
    return {
      lines,
      get cursorLine(){return line},
      get col(){return col},
      push(records){
        for(const record of records){
          if(!includeTx&&record.direction==='tx')continue;
          const text=decoder.decode(bytesOf(record),{stream:true});
          if(text)consume(text);
        }
      },
      clear(){
        lines.length=0;line=makeLine();lines.push(line);col=0;
      }
    };
  }
  const api={bytesOf,hexOf,createTerminal};root.DevToolTranscript=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
