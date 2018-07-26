$( "#dist_header" ).click(function() {
    switch (dist) {
        case "6h":
            dist = "12h";
            $("#dist_header").text("12ч");
            break;
        case "12h":
            dist = "24h";
            $("#dist_header").text("24h");
            break;
        case "24h":
            dist = "6h";
            $("#dist_header").text("6h");
            break;
        default:
            break;
    }
    // alert( "Handler for .click() called." );
});

$("#add_member3").click(function(){
    $("#member3").removeClass('d-none');
    $("#add_member3").parent().addClass('d-none');
    ucount = 3;
});
$("#add_member4").click(function(){
    $("#member4").removeClass('d-none');
    $("#add_member4").parent().addClass('d-none');
    $("#del_member3").parent().addClass('d-none');
    ucount = 4;
});
$("#add_member5").click(function(){
    $("#member5").removeClass('d-none');
    $("#add_member5").parent().addClass('d-none');
    $("#del_member4").parent().addClass('d-none');
    ucount = 5;
});
$("#add_member6").click(function(){
    $("#member6").removeClass('d-none');
    $("#add_member6").parent().addClass('d-none');
    $("#del_member5").parent().addClass('d-none');
    ucount = 6;
});

$("#del_member3").click(function(){
    $("#member3").addClass('d-none');
    $("#add_member3").parent().removeClass('d-none');
    ucount = 2;
});
$("#del_member4").click(function(){
    $("#member4").addClass('d-none');
    $("#add_member4").parent().removeClass('d-none');
    $("#del_member3").parent().removeClass('d-none');
    ucount = 3;
});
$("#del_member5").click(function(){
    $("#member5").addClass('d-none');
    $("#add_member5").parent().removeClass('d-none');
    $("#del_member4").parent().removeClass('d-none');
    ucount = 4;
});
$("#del_member6").click(function(){
    $("#member6").addClass('d-none');
    $("#add_member6").parent().removeClass('d-none');
    $("#del_member5").parent().removeClass('d-none');
    ucount = 5;
});